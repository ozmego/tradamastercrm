"""Backtest, profiller, baseline'lar ve veri kaynaklari."""

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from tradecrm.api.context import AppContext
from tradecrm.api.deps import Auth, current_user, get_ctx, get_db, require_csrf
from tradecrm.api.routers.activity import tail_jsonl
from tradecrm.api.services.users import record_action
from tradecrm.backtest.service import BacktestParams, InvalidParamsError
from tradecrm.db.models import BacktestRun
from tradecrm.db.repository import _as_utc

router = APIRouter(tags=["research"])


@router.get("/profiles")
def profiles(_: Auth = Depends(current_user)) -> list[dict]:
    from tradecrm.strategy.profiles import PROFILES

    return [{"name": p.name, "description": p.description, "strategy": p.strategy_name,
             "sources": sorted(p.required_sources)} for p in PROFILES.values()]


class BacktestIn(BaseModel):
    profile: str = Field(max_length=200)
    symbols: list[str] = Field(min_length=1, max_length=20)
    timeframe: str = Field(default="4h", max_length=4)
    days: int = 1095
    train_days: int = 180
    test_days: int = 60
    optimize: bool = False
    sensitivity: bool = False
    slippage_bps: float = 5.0


def _run_row(r: BacktestRun, with_result: bool = False) -> dict:
    d = {
        "id": r.id, "status": r.status, "params": json.loads(r.params), "error": r.error, "created_by": r.created_by,
        "created_at": _as_utc(r.created_at).isoformat(),
        "started_at": _as_utc(r.started_at).isoformat() if r.started_at else None,
        "finished_at": _as_utc(r.finished_at).isoformat() if r.finished_at else None,
    }
    if r.result:
        result = json.loads(r.result)
        d["summary"] = {"gate_passed": result["gate"]["passed"], "pooled": result["pooled"]}
        if with_result:
            result.pop("returns", None)
            d["result"] = result
    return d


@router.post("/backtests")
def create_backtest(body: BacktestIn, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                    db: Session = Depends(get_db)) -> dict:
    if ctx.backtests is None:
        raise HTTPException(status_code=503, detail="backtest kuyrugu yok")
    try:
        run_id = ctx.backtests.submit(BacktestParams(**body.model_dump()), auth.username)
    except InvalidParamsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    record_action(db, auth.username, "backtest_start", detail={"id": run_id, **body.model_dump()})
    return {"id": run_id}


@router.get("/backtests")
def list_backtests(limit: int = Query(50, ge=1, le=200), _: Auth = Depends(current_user),
                   db: Session = Depends(get_db)) -> list[dict]:
    rows = db.exec(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit))
    return [_run_row(r) for r in rows]


@router.get("/backtests/{run_id}")
def get_backtest(run_id: int, _: Auth = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest bulunamadi")
    return _run_row(run, with_result=True)


@router.post("/backtests/{run_id}/cancel")
def cancel_backtest(run_id: int, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                    db: Session = Depends(get_db)) -> dict:
    run = db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest bulunamadi")
    if ctx.backtests is None or not ctx.backtests.cancel(run_id):
        raise HTTPException(status_code=409, detail="yalnizca sirada bekleyen backtest iptal edilebilir")
    record_action(db, auth.username, "backtest_cancel", detail={"id": run_id})
    db.refresh(run)
    return _run_row(run)


@router.post("/backtests/{run_id}/save-baseline")
def save_baseline_from_run(run_id: int, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                           db: Session = Depends(get_db)) -> dict:
    from tradecrm.monitor.drift import Baseline, save_baseline

    run = db.get(BacktestRun, run_id)
    if run is None or run.status != "DONE" or not run.result:
        raise HTTPException(status_code=400, detail="yalnizca tamamlanmis backtest'ten baseline kaydedilir")
    params = json.loads(run.params)
    if "+" in params["profile"]:
        raise HTTPException(status_code=400, detail="portfoy icin degil, her uye profil icin ayri kaydedilir")
    result = json.loads(run.result)
    path = save_baseline(Baseline(
        profile=params["profile"], timeframe=params["timeframe"], symbols=params["symbols"],
        returns=result["returns"], created_at=datetime.now(UTC).isoformat(), metrics=result["pooled"],
        gate_passed=result["gate"]["passed"],
    ), root=ctx.baseline_dir)
    record_action(db, auth.username, "baseline_saved", detail={"run_id": run_id, "path": str(path)})
    return {"ok": True, "n": len(result["returns"]), "file": path.name}


@router.get("/baselines")
def baselines(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx)) -> list[dict]:
    out = []
    for p in sorted(Path(ctx.baseline_dir).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rets = d.get("returns") or []
        out.append({"file": p.name, "profile": d.get("profile"), "timeframe": d.get("timeframe"),
                    "symbols": d.get("symbols"), "n": len(rets), "mean": sum(rets) / len(rets) if rets else None,
                    "gate_passed": d.get("gate_passed"), "created_at": d.get("created_at")})
    return out


# ------------------------------------------------------------- veri kaynaklari
@router.get("/data/coverage")
def data_coverage(_: Auth = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    from tradecrm.data.collectors.derivatives import coverage

    return [{"symbol": s, "metric": m, "rows": n, "from": _as_utc(lo).isoformat(), "to": _as_utc(hi).isoformat()}
            for s, m, n, lo, hi in coverage(db)]


@router.get("/data/sources")
def data_sources(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx)) -> dict:
    """Son audit kayitlarindan kaynak sagligi: en son hata ve son basarili tick."""
    records = tail_jsonl(Path(ctx.audit_log_path))
    failures: dict[str, dict] = {}
    for r in records:
        if r.get("event_type") == "source_unavailable":
            src = str(r.get("source") or r.get("sources") or "?")
            failures[src] = {"source": src, "last_error_at": r.get("timestamp"), "error": r.get("error"),
                             "count": failures.get(src, {}).get("count", 0) + 1}
    last_tick = next((r.get("timestamp") for r in reversed(records) if r.get("event_type") == "signal_evaluated"), None)
    problems = next((r.get("data_problems") for r in reversed(records) if r.get("event_type") == "signal_evaluated"),
                    None)
    return {"failures": list(failures.values()), "last_tick_at": last_tick, "last_data_problems": problems}
