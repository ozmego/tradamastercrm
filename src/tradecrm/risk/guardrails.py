"""RiskManager: her emirden once (paper + live) senkron cagrilan tek
zorunlu gecis noktasi. target_sizing'in onerisinden bagimsiz olarak
%75 toplam sermaye tavanini ve kill-switch/TRADING_ENABLED durumunu
yeniden dogrular.
"""

from dataclasses import dataclass

from tradecrm.risk.state import RiskStateStore
from tradecrm.strategy.base import Signal


@dataclass
class OrderCheckResult:
    allowed: bool
    reason: str
    approved_capital_fraction: float = 0.0  # TOPLAM sermayenin onaylanan orani (0..0.75)


class RiskManager:
    def __init__(
        self,
        state: RiskStateStore,
        total_capital_allocation_pct: float = 0.75,
        max_daily_loss_pct: float = 0.05,
    ) -> None:
        if not (0.0 < total_capital_allocation_pct <= 1.0):
            raise ValueError("total_capital_allocation_pct (0, 1] araliginda olmali")
        self.state = state
        self.total_capital_allocation_pct = total_capital_allocation_pct
        self.max_daily_loss_pct = abs(max_daily_loss_pct)

    def check_order(
        self,
        side: Signal,
        proposed_pool_fraction: float = 0.0,
        is_first_live_order: bool = False,
    ) -> OrderCheckResult:
        """side=SELL (pozisyon kapatma) kill-switch/TRADING_ENABLED tarafindan
        engellenmez -- bir cikisi bastirmak, kotu bir durumu daha kotu hale
        getirebilir (news_aware.py'deki SELL-passthrough felsefesiyle ayni
        mantik). side=BUY (yeni pozisyon) butun guvenlik kontrollerinden gecer.
        """
        if side is Signal.SELL:
            return OrderCheckResult(True, "sell_always_allowed", approved_capital_fraction=1.0)

        if not self.state.is_trading_enabled():
            return OrderCheckResult(False, "trading_disabled")
        if self.state.is_kill_switch_tripped():
            return OrderCheckResult(False, f"daily_loss_kill_switch_tripped: {self.state.kill_switch_reason}")
        if is_first_live_order and not self.state.is_first_order_confirmed():
            return OrderCheckResult(False, "first_live_order_not_confirmed")

        # target_sizing'in ciktisindan bagimsiz, ikinci bir savunma katmani:
        # ne olursa olsun havuz orani [0, 1] ve toplam sermaye orani %75'i
        # gecemez.
        clamped_pool_fraction = max(0.0, min(1.0, proposed_pool_fraction))
        approved_total_fraction = clamped_pool_fraction * self.total_capital_allocation_pct

        return OrderCheckResult(True, "ok", approved_capital_fraction=approved_total_fraction)

    def check_and_maybe_trip_daily_loss(self, realized_pnl_pct_today: float) -> bool:
        """Gunluk zarar limiti asildiysa kill-switch'i tetikler. Zaten
        tetiklenmisse tekrar tetiklemez (idempotent). Kill-switch'in
        tetiklenip tetiklenmedigini dondurur."""
        if realized_pnl_pct_today <= -self.max_daily_loss_pct:
            if not self.state.is_kill_switch_tripped():
                self.state.trip_kill_switch(
                    reason=f"gunluk P&L {realized_pnl_pct_today:.4%} <= -{self.max_daily_loss_pct:.4%}"
                )
            return True
        return False
