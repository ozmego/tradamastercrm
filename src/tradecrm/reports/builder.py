"""Donem raporu: belirli bir zaman araliginda (UTC) ne oldu?

Rapor olusturuldugu anin donmus goruntusudur (JSON olarak saklanir); sonradan
veri degisse de ayni rapor ayni kalir. Fiyat istemez -- sadece DB'deki
kayitlardan uretilir (gecmis bir donem icin "anlik fiyat" anlamsizdir).

Bolumler: ozet (kapanan islemler), equity (EquitySnapshot), gunluk K/Z,
sembol / cikis nedeni dagilimi, islem listesi, acilan islemler, risk olaylari,
veri toplayici calismalari, panel islemleri.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, select

from tradecrm.db.models import (
    AdminAction,
    CollectorRun,
    EquitySnapshot,
    RiskEvent,
    Trade,
)
from tradecrm.db.repository import _as_utc

KINDS = ("day", "week", "month", "custom")
MAX_DAYS = 3660
MAX_TRADES = 2000
MAX_EQUITY_POINTS = 400


class ReportParamsError(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    kind: str
    start: datetime  # dahil
    end: datetime  # haric
    label: str


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def resolve_period(kind: str, start: date | None = None, end: date | None = None,
                   today: date | None = None) -> Period:
    """Gun sinirlari UTC. end: dahil son gun (tarih); ic temsil haric uc.
    - day: start (varsayilan dun)
    - week: start'in bulundugu ISO haftasi (varsayilan gecen hafta, Pzt-Paz)
    - month: start'in ayi (varsayilan gecen ay)
    - custom: start..end (ikisi de gerekli)"""
    today = today or datetime.now(UTC).date()
    if kind not in KINDS:
        raise ReportParamsError(f"gecersiz rapor turu: {kind}")
    if kind == "day":
        d = start or today - timedelta(days=1)
        s, e = d, d
        label = f"Gunluk rapor {d:%d.%m.%Y}"
    elif kind == "week":
        ref = start or today - timedelta(days=7)
        s = ref - timedelta(days=ref.weekday())
        e = s + timedelta(days=6)
        label = f"Haftalik rapor {s:%d.%m} - {e:%d.%m.%Y}"
    elif kind == "month":
        ref = start or (today.replace(day=1) - timedelta(days=1))
        s = ref.replace(day=1)
        nxt = (s.replace(day=28) + timedelta(days=4)).replace(day=1)
        e = nxt - timedelta(days=1)
        label = f"Aylik rapor {s:%m.%Y}"
    else:
        if start is None or end is None:
            raise ReportParamsError("ozel donem icin baslangic ve bitis tarihi gerekli")
        s, e = start, end
        label = f"Donem raporu {s:%d.%m.%Y} - {e:%d.%m.%Y}"
    if e < s:
        raise ReportParamsError("bitis tarihi baslangictan once olamaz")
    if (e - s).days + 1 > MAX_DAYS:
        raise ReportParamsError(f"donem en fazla {MAX_DAYS} gun olabilir")
    if s > today:
        raise ReportParamsError("donem gelecekte basliyor")
    return Period(kind, _midnight(s), _midnight(e) + timedelta(days=1), label)


def _iso(dt: datetime | None) -> str | None:
    return _as_utc(dt).isoformat() if dt else None


def _account_filter(col, account: str | None):
    if not account:
        return None
    if account.endswith("*"):
        return col.startswith(account[:-1])
    return col == account


def _where(stmt, col, account):
    clause = _account_filter(col, account)
    return stmt.where(clause) if clause is not None else stmt


def _trade_row(t: Trade) -> dict:
    ret = (t.exit_price / t.entry_price - 1.0) if t.exit_price and t.entry_price else None
    held = ((_as_utc(t.exit_time) - _as_utc(t.entry_time)).total_seconds() / 3600) if t.exit_time else None
    return {
        "id": t.id, "account": t.broker, "symbol": t.symbol, "entry_time": _iso(t.entry_time),
        "entry_price": t.entry_price, "exit_time": _iso(t.exit_time), "exit_price": t.exit_price, "qty": t.qty,
        "pnl": t.realized_pnl, "return_pct": ret, "fees": t.fees, "exit_reason": t.exit_reason,
        "hours_held": held,
    }


def _summary(closed: list[Trade]) -> dict:
    pnls = [t.realized_pnl or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    held = [(_as_utc(t.exit_time) - _as_utc(t.entry_time)).total_seconds() / 3600 for t in closed]
    best = max(closed, key=lambda t: t.realized_pnl or 0.0) if closed else None
    worst = min(closed, key=lambda t: t.realized_pnl or 0.0) if closed else None
    # Ardisik kayip serisi (en uzun)
    streak = longest = 0
    for p in pnls:
        streak = streak + 1 if p < 0 else 0
        longest = max(longest, streak)
    return {
        "closed": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "realized": sum(pnls), "gross_win": gross_win, "gross_loss": gross_loss,
        "fees": sum(t.fees or 0.0 for t in closed),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "expectancy": sum(pnls) / len(closed) if closed else None,
        "avg_win": gross_win / len(wins) if wins else None,
        "avg_loss": -gross_loss / len(losses) if losses else None,
        "avg_hours_held": sum(held) / len(held) if held else None,
        "longest_losing_streak": longest,
        "best": _trade_row(best) if best is not None and (best.realized_pnl or 0) > 0 else None,
        "worst": _trade_row(worst) if worst is not None and (worst.realized_pnl or 0) < 0 else None,
    }


def _downsample(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    step = len(points) / n
    out = [points[int(i * step)] for i in range(n)]
    out[-1] = points[-1]
    return out


def _equity(db: Session, period: Period, account: str | None) -> list[dict]:
    stmt = select(EquitySnapshot).where(EquitySnapshot.ts >= period.start, EquitySnapshot.ts < period.end)
    stmt = _where(stmt, EquitySnapshot.account, account).order_by(EquitySnapshot.ts)
    by_acc: dict[str, list[EquitySnapshot]] = defaultdict(list)
    for row in db.exec(stmt):
        by_acc[row.account].append(row)
    out = []
    for acc, rows in sorted(by_acc.items()):
        eq = [r.equity for r in rows]
        peak, max_dd = eq[0], 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, 1 - v / peak)
        out.append({
            "account": acc, "start": eq[0], "end": eq[-1],
            "change": eq[-1] - eq[0], "return_pct": (eq[-1] / eq[0] - 1) if eq[0] else None,
            "max_drawdown": max_dd, "high": max(eq), "low": min(eq),
            "points": _downsample([{"t": _iso(r.ts), "v": r.equity} for r in rows], MAX_EQUITY_POINTS),
        })
    return out


def build_report(db: Session, period: Period, account: str | None = None, now: datetime | None = None,
                 created_by: str = "") -> dict:
    now = now or datetime.now(UTC)

    cq = select(Trade).where(Trade.status == "CLOSED", Trade.exit_time >= period.start, Trade.exit_time < period.end)
    closed = sorted(db.exec(_where(cq, Trade.broker, account)), key=lambda t: _as_utc(t.exit_time))
    oq = select(Trade).where(Trade.entry_time >= period.start, Trade.entry_time < period.end)
    opened = list(db.exec(_where(oq, Trade.broker, account)))
    # Donem sonunda hala acik olanlar: donem bitmeden acilmis, (henuz) kapanmamis ya da donemden sonra kapanmis.
    still_q = select(Trade).where(Trade.entry_time < period.end)
    still_open = [t for t in db.exec(_where(still_q, Trade.broker, account))
                  if t.exit_time is None or _as_utc(t.exit_time) >= period.end]

    # Gunluk K/Z (bos gunler dahil)
    daily_map: dict[str, dict] = {}
    day = period.start
    while day < period.end:
        daily_map[day.date().isoformat()] = {"date": day.date().isoformat(), "pnl": 0.0, "trades": 0, "wins": 0}
        day += timedelta(days=1)
    for t in closed:
        d = daily_map.get(_as_utc(t.exit_time).date().isoformat())
        if d is not None:
            d["pnl"] += t.realized_pnl or 0.0
            d["trades"] += 1
            d["wins"] += (t.realized_pnl or 0.0) > 0
    cum = 0.0
    daily = []
    for d in daily_map.values():
        cum += d["pnl"]
        daily.append({**d, "cum": cum})

    sym: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "fees": 0.0})
    reasons: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0})
    for t in closed:
        p = t.realized_pnl or 0.0
        s = sym[t.symbol]
        s["pnl"] += p
        s["trades"] += 1
        s["wins"] += p > 0
        s["fees"] += t.fees or 0.0
        r = reasons[t.exit_reason or "unknown"]
        r["pnl"] += p
        r["count"] += 1
        r["wins"] += p > 0

    rq = select(RiskEvent).where(RiskEvent.timestamp >= period.start, RiskEvent.timestamp < period.end)
    if account:
        # Alt hesap ("paper:x") kok hesabin ("paper") olaylarindan da etkilenir.
        root = account.rstrip("*").split(":")[0]
        if account.endswith("*"):
            rq = rq.where(RiskEvent.account.startswith(root))
        else:
            rq = rq.where(RiskEvent.account.in_(sorted({account, root})))
    risk_events = [{"ts": _iso(e.timestamp), "account": e.account, "type": e.event_type, "reason": e.reason}
                   for e in db.exec(rq.order_by(RiskEvent.timestamp))]

    runs = list(db.exec(select(CollectorRun).where(CollectorRun.started_at >= period.start,
                                                   CollectorRun.started_at < period.end)))
    collector = {
        "runs": len(runs),
        "ok": sum(r.status == "OK" for r in runs),
        "partial": sum(r.status == "PARTIAL" for r in runs),
        "failed": sum(r.status in ("FAILED", "INTERRUPTED") for r in runs),
        "rows_added": sum(r.rows_added for r in runs),
        "rows_purged": sum(r.rows_purged for r in runs),
    }

    actions = list(db.exec(select(AdminAction).where(AdminAction.ts >= period.start, AdminAction.ts < period.end)
                           .order_by(AdminAction.ts.desc()).limit(200)))
    admin_actions = [{"ts": _iso(a.ts), "username": a.username, "action": a.action, "account": a.account,
                      "ok": a.ok, "detail": json.loads(a.detail) if a.detail else None} for a in actions]

    return {
        "version": 1,
        "title": period.label + (f" - {account}" if account else ""),
        "kind": period.kind,
        "account": account,
        "period": {"start": period.start.isoformat(), "end": period.end.isoformat(),
                   "days": (period.end - period.start).days, "complete": period.end <= now},
        "generated_at": now.isoformat(),
        "generated_by": created_by,
        "summary": {**_summary(closed), "opened": len(opened), "open_at_end": len(still_open)},
        "equity": _equity(db, period, account),
        "daily": daily,
        "by_symbol": sorted(({"symbol": k, **v} for k, v in sym.items()), key=lambda r: r["pnl"], reverse=True),
        "by_reason": sorted(({"reason": k, **v} for k, v in reasons.items()), key=lambda r: r["count"], reverse=True),
        "trades": [_trade_row(t) for t in closed[-MAX_TRADES:]],
        "trades_truncated": max(0, len(closed) - MAX_TRADES),
        "open_at_end": [_trade_row(t) for t in still_open][:200],
        "risk_events": risk_events,
        "collector": collector,
        "admin_actions": admin_actions,
    }
