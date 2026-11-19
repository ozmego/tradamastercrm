"""Toplayici izleme: saglik, sembol bazinda veri tazeligi, calisma gecmisi, log."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.api.deps import Auth, current_user, get_ctx, get_db, require_csrf
from tradecrm.api.services.collectors import CollectorBusyError
from tradecrm.api.services.users import record_action
from tradecrm.monitor.collectors import (
    COLLECTORS,
    collector_health,
    list_runs,
    run_to_dict,
)

router = APIRouter(tags=["collectors"])

RUN_STATUSES = ("RUNNING", "OK", "PARTIAL", "FAILED", "INTERRUPTED")


def _process_for(ctx: AppContext, process_name: str, tail: int = 0):
    proc = ctx.collector if process_name == "collector" else None
    return proc.status(tail=tail) if proc is not None else None


def health_for(db: Session, ctx: AppContext, name: str) -> dict:
    spec = COLLECTORS[name]
    st = _process_for(ctx, spec.process)
    return collector_health(
        db, spec, process_running=bool(st and st.running), process_pid=st.pid if st else None,
        process_started_at=st.started_at if st else None, symbols=ctx.collect_symbols,
        retention_days=ctx.collect_retention_days,
    )


def _spec_or_404(name: str):
    if name not in COLLECTORS:
        raise HTTPException(status_code=404, detail="toplayici bulunamadi")
    return COLLECTORS[name]


@router.get("/collectors")
def list_collectors(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                    db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for name in COLLECTORS:
        h = health_for(db, ctx, name)
        h.pop("freshness")
        out.append(h)
    return out


@router.get("/collectors/{name}")
def get_collector(name: str, _: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                  db: Session = Depends(get_db)) -> dict:
    spec = _spec_or_404(name)
    h = health_for(db, ctx, name)
    st = _process_for(ctx, spec.process, tail=120)
    h["log"] = st.last_log_lines if st else []
    return h


@router.get("/collectors/{name}/runs")
def get_runs(name: str, limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
             status: str | None = Query(None), _: Auth = Depends(current_user),
             db: Session = Depends(get_db)) -> dict:
    _spec_or_404(name)
    if status is not None and status not in RUN_STATUSES:
        raise HTTPException(status_code=400, detail="gecersiz durum")
    rows, total = list_runs(db, name, limit=limit, offset=offset, status=status)
    return {"items": [run_to_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.post("/collectors/{name}/run", status_code=202)
def trigger_run(name: str, auth: Auth = Depends(require_csrf), ctx: AppContext = Depends(get_ctx),
                db: Session = Depends(get_db)) -> dict:
    _spec_or_404(name)
    if ctx.collector_trigger is None:
        raise HTTPException(status_code=503, detail="elle calistirma kullanilamiyor")
    try:
        ctx.collector_trigger.submit(name)
    except CollectorBusyError:
        record_action(db, auth.username, "collector_run", detail={"collector": name}, ok=False)
        raise HTTPException(status_code=409, detail="bu toplayici zaten calisiyor") from None
    record_action(db, auth.username, "collector_run", detail={"collector": name})
    return {"accepted": True}
