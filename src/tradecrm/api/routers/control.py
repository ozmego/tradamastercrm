"""Durum degistiren uclar: risk bayraklari ve arka plan surecleri.
Hepsi oturum + CSRF ister ve AdminAction'a yazilir."""

import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.api.deps import Auth, current_user, get_ctx, get_db, require_csrf
from tradecrm.api.services import risk_actions
from tradecrm.api.services.live import LiveStartError, prepare_live_start
from tradecrm.api.services.users import record_action

router = APIRouter(tags=["control"])


# --------------------------------------------------------------------- risk
class TradingIn(BaseModel):
    enabled: bool
    confirm: str = Field(max_length=128)
    note: str | None = Field(default=None, max_length=200)


class ConfirmIn(BaseModel):
    confirm: str = Field(max_length=128)


class TripIn(ConfirmIn):
    reason: str = Field(default="manuel", max_length=200)


def _run(fn, *args):
    try:
        fn(*args)
    except risk_actions.RiskActionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return {"ok": True}


@router.post("/risk/{account}/trading")
def set_trading(account: str, body: TradingIn, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    return _run(risk_actions.set_trading, db, auth.username, account, body.enabled, body.confirm, body.note)


@router.post("/risk/{account}/kill-switch/trip")
def trip(account: str, body: TripIn, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    return _run(risk_actions.trip_kill_switch, db, auth.username, account, body.confirm, body.reason)


@router.post("/risk/{account}/kill-switch/reset")
def reset(account: str, body: ConfirmIn, auth: Auth = Depends(require_csrf), db: Session = Depends(get_db)) -> dict:
    return _run(risk_actions.reset_kill_switch, db, auth.username, account, body.confirm)


# ------------------------------------------------------------------ surecler
def _proc_json(proc, tail: int = 40) -> dict:
    st = proc.status(tail=tail)
    d = asdict(st)
    d.pop("command", None)  # komut satiri yol bilgisi icerir; meta yeterli
    d["log"] = d.pop("last_log_lines")
    return d


@router.get("/processes")
def processes(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx)) -> dict:
    last = ctx.live.last_start() if hasattr(ctx.live, "last_start") else None
    return {
        "collector": _proc_json(ctx.collector),
        "live": _proc_json(ctx.live),
        "live_last": last,
        # Panelin kendisi: arayuz onun uzerinde calistigi icin buradan durdurulamaz.
        "admin": {"running": True, "pid": os.getpid(), "started_at": ctx.started_at},
        # Canli (gercek hesap) runner'lari; emir salteri .env'de (panelden degistirilemez).
        "exchange_runners": {
            eid: {**_proc_json(proc), "last": proc.last_start() if hasattr(proc, "last_start") else None,
                  "env_orders_open": ctx.live_trading_allowed_by_env}
            for eid, proc in ctx.exchange_runners.items()
        },
    }


# ------------------------------------------------------- otomatik baslatma
_AUTOSTART_ACTIONS = ("enable", "disable")


@router.get("/autostart")
def autostart_status(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx)) -> dict:
    svc = ctx.autostart
    if svc is None or not svc.supported():
        return {"supported": False, "error": None, "tasks": {}}
    try:
        tasks = svc.status_all()
    except Exception as exc:  # noqa: BLE001 -- PowerShell/Gorev Zamanlayici hatasi paneli bozmasin
        return {"supported": True, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "tasks": {}}
    names = {k: t.task_name for k, t in svc.tasks.items()}
    return {"supported": True, "error": None,
            "tasks": {k: {**v, "task_name": names.get(k)} for k, v in tasks.items()}}


@router.post("/autostart/{key}/{action}")
def autostart_action(key: str, action: str, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                     db: Session = Depends(get_db)) -> dict:
    svc = ctx.autostart
    if svc is None or not svc.supported():
        raise HTTPException(status_code=400, detail="otomatik baslatma bu sistemde desteklenmiyor (yalnizca Windows)")
    if key not in svc.tasks or action not in _AUTOSTART_ACTIONS:
        raise HTTPException(status_code=404, detail="bilinmeyen surec ya da islem")
    if key.startswith("live_") and action == "enable":
        proc = ctx.exchange_runners.get(key[5:])
        last = proc.last_start() if proc is not None and hasattr(proc, "last_start") else None
        if not (last and (last.get("meta") or {}).get("profile")):
            raise HTTPException(status_code=409, detail=(
                f"{key[5:]} runner hic baslatilmamis: once komut satirindan 'tradecrm live start --mode {key[5:]} ...'"))
    if key == "live" and action == "enable":
        last = ctx.live.last_start() if hasattr(ctx.live, "last_start") else None
        if not (last and (last.get("meta") or {}).get("profile")):
            raise HTTPException(status_code=409, detail=(
                "paper runner hic baslatilmamis: otomatik baslatma son parametreleri kullanir, once bir kez baslatin"))
    try:
        result = svc.enable(key) if action == "enable" else {"removed": svc.disable(key)}
    except (RuntimeError, ValueError) as exc:
        record_action(db, auth.username, f"autostart_{action}", detail={"process": key}, ok=False)
        raise HTTPException(status_code=502, detail=f"Gorev Zamanlayici: {str(exc)[:200]}") from None
    record_action(db, auth.username, f"autostart_{action}", detail={"process": key})
    return {"ok": True, "result": result}


@router.post("/processes/collector/{action}")
def collector_action(action: str, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                     db: Session = Depends(get_db)) -> dict:
    if action not in ("start", "stop"):
        raise HTTPException(status_code=404, detail="bilinmeyen islem")
    ctx.collector.start() if action == "start" else ctx.collector.stop()
    record_action(db, auth.username, f"collector_{action}")
    return _proc_json(ctx.collector)


class LiveStartIn(BaseModel):
    profile: str = Field(max_length=200)
    symbols: list[str] = Field(min_length=1, max_length=20)
    timeframe: str = Field(max_length=4)
    initial_capital: float = Field(ge=100, le=10_000_000)


@router.post("/processes/live/start")
def live_start(body: LiveStartIn, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
               db: Session = Depends(get_db)) -> dict:
    if ctx.live.status(tail=0).running:
        raise HTTPException(status_code=409, detail="bir runner zaten calisiyor; once durdurun")
    try:
        command, meta = prepare_live_start(db, body.profile, body.symbols, body.timeframe, body.initial_capital)
    except LiveStartError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    meta["started_by"] = auth.username
    ctx.live.start(command=command, meta=meta)
    record_action(db, auth.username, "live_start", "paper", meta)
    return _proc_json(ctx.live)


@router.post("/processes/live/stop")
def live_stop(auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
              db: Session = Depends(get_db)) -> dict:
    ctx.live.stop()
    record_action(db, auth.username, "live_stop", "paper")
    return _proc_json(ctx.live)


# ------------------------------------------------- canli (gercek hesap) runner
@router.post("/processes/exchange/{exchange_id}/{action}")
def exchange_runner_action(exchange_id: str, action: str, auth: Auth = Depends(require_csrf),
                           ctx: AppContext = Depends(get_ctx), db: Session = Depends(get_db)) -> dict:
    """stop: durdurur. start: YALNIZCA son parametrelerle (komut satirinda verilmis) yeniden baslatir;
    parametre ve emir salterleri panelden degistirilemez."""
    from tradecrm.api.services.live import prepare_exchange_start

    proc = ctx.exchange_runners.get(exchange_id)
    if proc is None or action not in ("start", "stop"):
        raise HTTPException(status_code=404, detail="bilinmeyen runner ya da islem")
    if action == "stop":
        proc.stop()
        record_action(db, auth.username, "exchange_runner_stop", exchange_id)
        return _proc_json(proc)
    if proc.status(tail=0).running:
        raise HTTPException(status_code=409, detail="runner zaten calisiyor")
    meta = ((proc.last_start() or {}).get("meta")) or {}
    if not meta.get("profile"):
        raise HTTPException(status_code=409, detail=(
            f"son parametre yok: once komut satirindan 'tradecrm live start --mode {exchange_id} ...'"))
    try:
        command, new_meta = prepare_exchange_start(db, exchange_id, meta["profile"], list(meta.get("symbols") or []),
                                                   meta.get("timeframe", ""), meta.get("initial_capital"))
    except LiveStartError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    new_meta["started_by"] = auth.username
    proc.start(command=command, meta=new_meta)
    record_action(db, auth.username, "exchange_runner_start", exchange_id, new_meta)
    return _proc_json(proc)
