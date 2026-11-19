"""Raporlarin saklanmasi (Report tablosu)."""

import json
from datetime import UTC, date, datetime

from sqlmodel import Session, func, select

from tradecrm.db.models import Report
from tradecrm.db.repository import _as_utc
from tradecrm.reports.builder import build_report, resolve_period


def create_report(db: Session, kind: str, start: date | None = None, end: date | None = None,
                  account: str | None = None, created_by: str = "", now: datetime | None = None) -> Report:
    period = resolve_period(kind, start, end, today=(now or datetime.now(UTC)).date())
    data = build_report(db, period, account=account or None, now=now, created_by=created_by)
    row = Report(kind=kind, title=data["title"], account=account or None, period_start=period.start,
                 period_end=period.end, created_by=created_by, data=json.dumps(data))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def report_meta(row: Report) -> dict:
    data = json.loads(row.data)
    s = data["summary"]
    return {
        "id": row.id, "kind": row.kind, "title": row.title, "account": row.account,
        "period_start": _as_utc(row.period_start).isoformat(), "period_end": _as_utc(row.period_end).isoformat(),
        "created_at": _as_utc(row.created_at).isoformat(), "created_by": row.created_by,
        "sent_at": _as_utc(row.sent_at).isoformat() if row.sent_at else None,
        "realized": s["realized"], "closed": s["closed"], "win_rate": s["win_rate"],
        "complete": data["period"]["complete"],
    }


def list_reports(db: Session, limit: int = 50, offset: int = 0, kind: str | None = None) -> tuple[list[Report], int]:
    q = select(Report)
    cq = select(func.count()).select_from(Report)
    if kind:
        q, cq = q.where(Report.kind == kind), cq.where(Report.kind == kind)
    rows = list(db.exec(q.order_by(Report.created_at.desc()).offset(offset).limit(limit)))
    return rows, int(db.exec(cq).one())


def report_full(row: Report) -> dict:
    return {**report_meta(row), "data": json.loads(row.data)}
