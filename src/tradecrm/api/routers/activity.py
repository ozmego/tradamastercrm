"""Islem, emir, risk olayi ve denetim gecmisi (salt okunur, sayfali)."""

import csv
import io
import json
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlmodel import Session, select

from tradecrm.api.context import AppContext
from tradecrm.api.deps import Auth, current_user, get_ctx, get_db
from tradecrm.db.models import AdminAction, Order, RiskEvent, Trade
from tradecrm.db.repository import _as_utc

router = APIRouter(tags=["activity"])


def _iso(dt: datetime | None) -> str | None:
    return _as_utc(dt).isoformat() if dt else None


def _page(db: Session, stmt, count_stmt, page: int, size: int):
    total = db.exec(count_stmt).one()
    rows = list(db.exec(stmt.offset((page - 1) * size).limit(size)))
    return rows, total


# ------------------------------------------------------------------ trades
def _trade_filters(stmt, account, symbol, status, date_from, date_to):
    if account:
        stmt = stmt.where(Trade.broker == account)
    if symbol:
        stmt = stmt.where(Trade.symbol == symbol)
    if status in ("OPEN", "CLOSED"):
        stmt = stmt.where(Trade.status == status)
    if date_from:
        stmt = stmt.where(Trade.entry_time >= date_from)
    if date_to:
        stmt = stmt.where(Trade.entry_time < date_to)
    return stmt


def _trade_row(t: Trade) -> dict:
    return {
        "id": t.id, "account": t.broker, "symbol": t.symbol, "strategy": t.strategy_name, "status": t.status,
        "entry_time": _iso(t.entry_time), "entry_price": t.entry_price, "qty": t.qty,
        "exit_time": _iso(t.exit_time), "exit_price": t.exit_price,
        "return_pct": (t.exit_price / t.entry_price - 1.0) if t.exit_price and t.entry_price else None,
        "realized_pnl": t.realized_pnl, "realized_pnl_pct": t.realized_pnl_pct, "fees": t.fees or t.entry_fees,
        "exit_reason": t.exit_reason, "signal_reason": t.signal_reason,
    }


@router.get("/trades")
def list_trades(
    account: str | None = None, symbol: str | None = None, status: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500),
    _: Auth = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    stmt = _trade_filters(select(Trade), account, symbol, status, date_from, date_to).order_by(Trade.entry_time.desc())
    count = _trade_filters(select(func.count()).select_from(Trade), account, symbol, status, date_from, date_to)
    rows, total = _page(db, stmt, count, page, size)
    closed_stmt = _trade_filters(select(Trade.realized_pnl, Trade.fees), account, symbol, "CLOSED", date_from, date_to)
    closed = list(db.exec(closed_stmt))
    wins = sum(1 for p, _f in closed if (p or 0) > 0)
    return {
        "items": [_trade_row(t) for t in rows], "total": total, "page": page, "size": size,
        "summary": {"closed": len(closed), "wins": wins, "net_pnl": sum(p or 0 for p, _f in closed),
                    "fees": sum(f or 0 for _p, f in closed)},
    }


@router.get("/trades.csv")
def export_trades(
    account: str | None = None, symbol: str | None = None, status: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    _: Auth = Depends(current_user), db: Session = Depends(get_db),
) -> StreamingResponse:
    stmt = _trade_filters(select(Trade), account, symbol, status, date_from, date_to).order_by(Trade.entry_time)
    rows = [_trade_row(t) for t in db.exec(stmt)]
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=tradecrm-trades.csv"})


# ------------------------------------------------------------------ orders
@router.get("/orders")
def list_orders(
    account: str | None = None, status: str | None = None, symbol: str | None = None,
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500),
    _: Auth = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    def flt(stmt):
        if account:
            stmt = stmt.where(Order.account == account)
        if status:
            stmt = stmt.where(Order.status == status.upper())
        if symbol:
            stmt = stmt.where(Order.symbol == symbol)
        return stmt

    rows, total = _page(db, flt(select(Order)).order_by(Order.created_at.desc()),
                        flt(select(func.count()).select_from(Order)), page, size)
    counts = dict(db.exec(select(Order.status, func.count()).group_by(Order.status)).all())
    return {
        "items": [{
            "id": o.id, "client_order_id": o.client_order_id, "account": o.account, "symbol": o.symbol,
            "side": o.side, "bar_time": _iso(o.bar_time), "status": o.status, "reason": o.reason,
            "requested_notional": o.requested_notional, "filled_qty": o.filled_qty, "avg_price": o.avg_price,
            "trade_id": o.trade_id, "error": o.error, "attempts": o.attempts,
            "created_at": _iso(o.created_at), "updated_at": _iso(o.updated_at),
        } for o in rows],
        "total": total, "page": page, "size": size, "status_counts": counts,
    }


# ------------------------------------------------------------- risk olaylari
@router.get("/risk/events")
def list_risk_events(
    account: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500),
    _: Auth = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    def flt(stmt):
        return stmt.where(RiskEvent.account == account) if account else stmt

    rows, total = _page(db, flt(select(RiskEvent)).order_by(RiskEvent.timestamp.desc(), RiskEvent.id.desc()),
                        flt(select(func.count()).select_from(RiskEvent)), page, size)
    return {"items": [{"id": e.id, "account": e.account, "event_type": e.event_type, "timestamp": _iso(e.timestamp),
                       "reason": e.reason} for e in rows], "total": total, "page": page, "size": size}


# ------------------------------------------------------------------- audit
@router.get("/audit/admin-actions")
def list_admin_actions(
    username: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500),
    _: Auth = Depends(current_user), db: Session = Depends(get_db),
) -> dict:
    def flt(stmt):
        return stmt.where(AdminAction.username == username) if username else stmt

    rows, total = _page(db, flt(select(AdminAction)).order_by(AdminAction.ts.desc(), AdminAction.id.desc()),
                        flt(select(func.count()).select_from(AdminAction)), page, size)
    return {"items": [{"id": a.id, "ts": _iso(a.ts), "username": a.username, "action": a.action,
                       "account": a.account, "detail": json.loads(a.detail) if a.detail else None, "ok": a.ok}
                      for a in rows], "total": total, "page": page, "size": size}


def tail_jsonl(path: Path, max_lines: int = 5000) -> list[dict]:
    """Dosyanin son satirlarini okur (butun dosyayi bellege almadan)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = deque(f, maxlen=max_lines)
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@router.get("/audit/log")
def audit_log(
    event_type: str | None = None, symbol: str | None = None, limit: int = Query(200, ge=1, le=1000),
    _: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
) -> dict:
    records = tail_jsonl(Path(ctx.audit_log_path))
    types = sorted({r.get("event_type", "") for r in records})
    if event_type:
        records = [r for r in records if r.get("event_type") == event_type]
    if symbol:
        records = [r for r in records if r.get("symbol") == symbol]
    return {"items": list(reversed(records[-limit:])), "event_types": types}
