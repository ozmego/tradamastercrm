"""Yeni giris oncesi devre kesiciler. Saf fonksiyon -- simulator ve
LiveRunner ayni kurallari uygular.

Butun yuzdeler, islemin acilisindaki hesap bakiyesine gore P&L'dir
(Trade.realized_pnl_pct ile ayni tanim). Equity = baslangic + kumulatif
GERCEKLESEN P&L (iki ortamda da ayni tanim -> parite).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class BreakerConfig:
    max_daily_loss_pct: float = 0.05  # gerceklesen + gerceklesmemis
    max_consecutive_losses: int | None = 3
    cooldown_hours: float = 24.0
    max_weekly_loss_pct: float | None = 0.08  # son 7 gun gerceklesen
    max_drawdown_pct: float | None = 0.15  # equity zirvesinden -> sert durdurma (manuel reset)
    max_trades_per_day: int | None = None
    # Gunun K/Z'si (gerceklesen + gerceklesmemis) bu orana ulasinca o UTC gunu yeni alim yok.
    # Pozisyon BUYUTMEZ; yalnizca kari korumak icin girisleri durdurur. Kill-switch tetiklemez.
    daily_profit_target_pct: float | None = None


@dataclass(frozen=True)
class ClosedTradeLite:
    exit_time: datetime
    pnl: float
    pnl_pct: float


@dataclass(frozen=True)
class BreakerResult:
    allowed: bool
    reason: str
    hard_stop: bool = False  # True: kill-switch'i tetikle (manuel reset gerekir)
    # Simulatorde engelin suresi: "halt" (pencere sonuna kadar), "day"
    # (UTC gun sonuna kadar -- canlida insanin ertesi gun reset'ini temsil
    # eder), "temporary" (kosul kendiliginden cozulur).
    scope: str = "temporary"


def evaluate_entry(
    trades: Sequence[ClosedTradeLite],
    now: datetime,
    initial_equity: float,
    cfg: BreakerConfig,
    unrealized_pnl_pct: float = 0.0,
) -> BreakerResult:
    """trades: exit_time'a gore sirali kapanmis islemler (drawdown icin,
    son kill-switch reset'inden itibaren olanlar verilmeli)."""
    today = now.date()
    today_trades = [t for t in trades if t.exit_time.date() == today]

    # 1) Equity zirvesinden drawdown (sert durdurma)
    if cfg.max_drawdown_pct is not None and trades:
        equity = peak = initial_equity
        for t in trades:
            equity += t.pnl
            peak = max(peak, equity)
        if peak > 0 and (peak - equity) / peak >= cfg.max_drawdown_pct:
            return BreakerResult(False, f"max_drawdown {(peak - equity) / peak:.2%}", hard_stop=True, scope="halt")

    # 2) Gunluk zarar (gerceklesmemis dahil)
    daily = sum(t.pnl_pct for t in today_trades) + unrealized_pnl_pct
    if daily <= -abs(cfg.max_daily_loss_pct):
        return BreakerResult(False, f"daily_loss {daily:.2%}", hard_stop=True, scope="day")

    # 2b) Gunluk kar hedefi doldu -> gun sonuna kadar yeni giris yok (sert durdurma degil)
    if cfg.daily_profit_target_pct is not None and daily >= abs(cfg.daily_profit_target_pct):
        return BreakerResult(False, f"daily_target_reached {daily:.2%}", scope="day")

    # 3) Haftalik zarar (kendiliginden cozulur: pencere kayinca)
    if cfg.max_weekly_loss_pct is not None:
        week_start = now - timedelta(days=7)
        weekly = sum(t.pnl_pct for t in trades if t.exit_time >= week_start)
        if weekly <= -abs(cfg.max_weekly_loss_pct):
            return BreakerResult(False, f"weekly_loss {weekly:.2%}")

    # 4) Art arda kayiplar -> soguma
    if cfg.max_consecutive_losses is not None and trades:
        streak = 0
        for t in reversed(trades):
            if t.pnl <= 0:
                streak += 1
            else:
                break
        if streak >= cfg.max_consecutive_losses:
            until = trades[-1].exit_time + timedelta(hours=cfg.cooldown_hours)
            if now < until:
                return BreakerResult(False, f"loss_streak_{streak}_cooldown_until_{until.isoformat()}")

    # 5) Gunluk islem sayisi
    if cfg.max_trades_per_day is not None and len(today_trades) >= cfg.max_trades_per_day:
        return BreakerResult(False, "max_trades_per_day")

    return BreakerResult(True, "ok")
