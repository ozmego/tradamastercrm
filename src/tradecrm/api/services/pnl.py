"""Kar/zarar panosu: secilen donemde kapanan islemlerin gerceklesen K/Z'si +
acik islemlerin anlik fiyatla gerceklesmemis K/Z'si.

- Gerceklesen K/Z: Trade.realized_pnl (komisyonlar dusulmus). Donem filtresi
  CIKIS zamanina gore (o donemde realize edilen).
- Gerceklesmemis K/Z: piyasa degeri - (giris fiyati x adet + giris komisyonu);
  pozisyon sayfasiyla ayni tanim. Fiyat alinamayan pozisyon toplama girmez,
  sayisi ayrica bildirilir.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from tradecrm.db.models import Trade
from tradecrm.db.repository import _as_utc, _broker_clause


def _iso(dt: datetime | None) -> str | None:
    return _as_utc(dt).isoformat() if dt else None


def _price_return(t: Trade) -> float | None:
    return (t.exit_price / t.entry_price - 1.0) if t.exit_price and t.entry_price else None


def open_position_row(t: Trade, price: float | None) -> dict:
    cost = t.entry_price * t.qty + (t.entry_fees or 0.0)
    mv = price * t.qty if price else None
    return {
        "id": t.id, "account": t.broker, "symbol": t.symbol,
        "entry_time": _iso(t.entry_time), "entry_price": t.entry_price, "qty": t.qty, "cost": cost,
        "price": price, "market_value": mv,
        "unrealized_pnl": (mv - cost) if mv is not None else None,
        "unrealized_pct": (mv / cost - 1.0) if mv is not None and cost else None,
        "stop": t.stop_price,
        "stop_distance_pct": (t.stop_price / price - 1.0) if price and t.stop_price else None,
        "hours_open": (datetime.now(UTC) - _as_utc(t.entry_time)).total_seconds() / 3600,
    }


def pnl_dashboard(db: Session, market, days: int = 30, account: str | None = None,
                  now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days) if days > 0 else None

    q = select(Trade).where(Trade.status == "CLOSED")
    if since is not None:
        q = q.where(Trade.exit_time >= since)
    if account:
        q = q.where(_broker_clause(account))
    closed = sorted(db.exec(q), key=lambda t: _as_utc(t.exit_time))

    oq = select(Trade).where(Trade.status == "OPEN")
    if account:
        oq = oq.where(_broker_clause(account))
    open_trades = list(db.exec(oq))

    prices: dict[str, float | None] = {}
    for t in open_trades:
        if t.symbol not in prices:
            try:
                prices[t.symbol] = float(market.fetch_latest_price(t.symbol))
            except Exception:  # noqa: BLE001 -- fiyat yoksa pozisyon yine listelenir
                prices[t.symbol] = None
    open_rows = sorted((open_position_row(t, prices[t.symbol]) for t in open_trades),
                       key=lambda r: r["unrealized_pnl"] if r["unrealized_pnl"] is not None else 0.0)

    pnls = [t.realized_pnl or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    realized = sum(pnls)
    unrealized_known = [r["unrealized_pnl"] for r in open_rows if r["unrealized_pnl"] is not None]
    unrealized = sum(unrealized_known)
    held_hours = [(_as_utc(t.exit_time) - _as_utc(t.entry_time)).total_seconds() / 3600 for t in closed]

    def trade_ref(t: Trade | None) -> dict | None:
        if t is None:
            return None
        return {"id": t.id, "symbol": t.symbol, "account": t.broker, "pnl": t.realized_pnl,
                "return_pct": _price_return(t), "exit_time": _iso(t.exit_time)}

    best = max(closed, key=lambda t: t.realized_pnl or 0.0) if closed else None
    worst = min(closed, key=lambda t: t.realized_pnl or 0.0) if closed else None

    summary = {
        "total": realized + unrealized,
        "realized": realized,
        "unrealized": unrealized,
        "unpriced_positions": len(open_rows) - len(unrealized_known),
        "fees": sum(t.fees or 0.0 for t in closed),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "avg_win": gross_win / len(wins) if wins else None,
        "avg_loss": -gross_loss / len(losses) if losses else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "expectancy": realized / len(closed) if closed else None,
        "avg_hours_held": sum(held_hours) / len(held_hours) if held_hours else None,
        "open": len(open_rows),
        "open_cost": sum(r["cost"] for r in open_rows),
        "best": trade_ref(best if best is not None and (best.realized_pnl or 0) > 0 else None),
        "worst": trade_ref(worst if worst is not None and (worst.realized_pnl or 0) < 0 else None),
    }

    # Kumulatif gerceklesen K/Z: donem basinda (ya da ilk islemin girisinde) 0'dan baslar.
    cumulative = []
    if closed:
        start = since or _as_utc(min(t.entry_time for t in closed))
        cumulative.append({"t": start.isoformat(), "cum": 0.0, "pnl": 0.0, "symbol": None, "id": None})
        cum = 0.0
        for t in closed:
            cum += t.realized_pnl or 0.0
            cumulative.append({"t": _iso(t.exit_time), "cum": cum, "pnl": t.realized_pnl or 0.0,
                               "symbol": t.symbol, "id": t.id})

    by_symbol: dict[str, dict] = defaultdict(lambda: {"realized": 0.0, "unrealized": 0.0, "closed": 0, "wins": 0,
                                                      "open": 0})
    for t in closed:
        row = by_symbol[t.symbol]
        row["realized"] += t.realized_pnl or 0.0
        row["closed"] += 1
        row["wins"] += (t.realized_pnl or 0.0) > 0
    for r in open_rows:
        row = by_symbol[r["symbol"]]
        row["unrealized"] += r["unrealized_pnl"] or 0.0
        row["open"] += 1
    symbols = sorted(({"symbol": s, **v, "total": v["realized"] + v["unrealized"]} for s, v in by_symbol.items()),
                     key=lambda r: r["total"], reverse=True)

    reasons: dict[str, dict] = defaultdict(lambda: {"count": 0, "pnl": 0.0, "wins": 0})
    for t in closed:
        r = reasons[t.exit_reason or "unknown"]
        r["count"] += 1
        r["pnl"] += t.realized_pnl or 0.0
        r["wins"] += (t.realized_pnl or 0.0) > 0
    by_reason = sorted(({"reason": k, **v} for k, v in reasons.items()), key=lambda r: r["count"], reverse=True)

    accounts = sorted({t.broker for t in closed} | {r["account"] for r in open_rows})
    return {
        "days": days, "account": account, "since": since.isoformat() if since else None,
        "summary": summary, "cumulative": cumulative, "by_symbol": symbols, "by_reason": by_reason,
        "open_positions": open_rows, "accounts": accounts, "generated_at": now.isoformat(),
    }
