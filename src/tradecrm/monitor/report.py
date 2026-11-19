"""Oneri 13: haftalik (ya da istenen donem) performans raporu -- Telegram'a uygun duz metin."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from tradecrm.monitor.drift import DriftResult, trade_return


def build_report(
    account: str,
    trades: list,
    snapshot=None,
    drift: DriftResult | None = None,
    trading_enabled: bool | None = None,
    kill_switch_reason: str = "",
    now: datetime | None = None,
    days: int = 7,
) -> str:
    """trades: donem icinde kapanan islemler (Trade benzeri nesneler)."""
    now = now or datetime.now(UTC)
    start = now - timedelta(days=days)
    lines = [f"TradeCRM [{account}] {days} gunluk rapor ({start:%Y-%m-%d} - {now:%Y-%m-%d} UTC)"]

    closed = [t for t in trades if t.exit_time is not None]
    pnl = sum(t.realized_pnl or 0.0 for t in closed)
    fees = sum(t.fees or 0.0 for t in closed)
    wins = [t for t in closed if (t.realized_pnl or 0.0) > 0]
    lines.append(
        f"Kapanan islem: {len(closed)}  kazanan: {len(wins)}  net P&L: {pnl:+,.2f} USDT  komisyon: {fees:,.2f}"
    )
    if closed:
        rets = [r for t in closed if (r := trade_return(t)) is not None]
        if rets:
            lines.append(f"Islem basina ort. fiyat getirisi: {sum(rets) / len(rets):+.2%}")
        reasons = Counter(t.exit_reason or "?" for t in closed)
        lines.append("Cikis nedenleri: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))
        best = max(closed, key=lambda t: t.realized_pnl or 0.0)
        worst = min(closed, key=lambda t: t.realized_pnl or 0.0)
        lines.append(f"En iyi: {best.symbol} {best.realized_pnl or 0:+,.2f}  En kotu: {worst.symbol} {worst.realized_pnl or 0:+,.2f}")

    if snapshot is not None:
        lines.append(
            f"Hesap: equity {snapshot.equity:,.2f}  nakit {snapshot.cash:,.2f}  "
            f"acik pozisyon {len(snapshot.positions)} ({snapshot.market_value:,.2f}, "
            f"gerceklesmemis {snapshot.unrealized_pnl:+,.2f})"
        )
    if trading_enabled is not None:
        lines.append(f"Yeni alim izni: {'ACIK' if trading_enabled else 'KAPALI'}")
    if kill_switch_reason:
        lines.append(f"KILL-SWITCH: {kill_switch_reason}")
    if drift is not None:
        lines.append(f"Drift: {drift.describe()}")
    return "\n".join(lines)
