"""RiskManager: her emirden once (paper + live) senkron cagrilan tek
zorunlu gecis noktasi.

Alim izni (hepsi gerekli):
  1. Hesabin DB bayragi acik (`tradecrm risk enable-trading --account ...`).
  2. Kill-switch tetiklenmemis (hesap ya da kok hesap).
  3. CANLI hesaplar icin ek olarak: .env TRADING_ENABLED=true VE
     DRY_RUN=false (ana salter -- DB bayragi tek basina canli emre
     yetmez) ve ilk canli emir manuel onaylanmis.
  4. Toplam pozisyon tavani: (acik pozisyonlarin piyasa degeri + yeni emir)
     <= total_capital_allocation_pct * portfoy degeri. Tavan TEK emre degil,
     butun acik pozisyonlara uygulanir.
  5. Yeni emir kullanilabilir nakdi asamaz.

Satis (pozisyon kapatma) bu kontrollerin hicbirine takilmaz: bir cikisi
bastirmak kotu bir durumu daha kotu hale getirebilir.
"""

from dataclasses import dataclass

from tradecrm.broker.base import AccountSnapshot
from tradecrm.risk.state import RiskStateStore
from tradecrm.strategy.base import Signal

# Binance spot minimum emir tutari ~5-10 USDT; altindaki emirler anlamsiz.
DEFAULT_MIN_NOTIONAL = 10.0


@dataclass
class OrderCheckResult:
    allowed: bool
    reason: str
    approved_capital_fraction: float = 0.0  # geriye uyumluluk (check_order)
    approved_notional: float = 0.0  # quote tutari (check_buy)


class RiskManager:
    def __init__(
        self,
        state: RiskStateStore,
        total_capital_allocation_pct: float = 0.75,
        max_daily_loss_pct: float = 0.05,
        live_trading_allowed_by_env: bool = False,
        min_notional: float = DEFAULT_MIN_NOTIONAL,
    ) -> None:
        if not (0.0 < total_capital_allocation_pct <= 1.0):
            raise ValueError("total_capital_allocation_pct (0, 1] araliginda olmali")
        self.state = state
        self.total_capital_allocation_pct = total_capital_allocation_pct
        self.max_daily_loss_pct = abs(max_daily_loss_pct)
        self.live_trading_allowed_by_env = live_trading_allowed_by_env
        self.min_notional = min_notional

    def _flag_checks(self, is_first_live_order: bool) -> OrderCheckResult | None:
        if getattr(self.state, "is_live", False) and not self.live_trading_allowed_by_env:
            return OrderCheckResult(False, "env_live_trading_disabled (TRADING_ENABLED=true ve DRY_RUN=false gerekli)")
        if not self.state.is_trading_enabled():
            return OrderCheckResult(False, "trading_disabled")
        if self.state.is_kill_switch_tripped():
            return OrderCheckResult(False, f"daily_loss_kill_switch_tripped: {self.state.kill_switch_reason}")
        if is_first_live_order and not self.state.is_first_order_confirmed():
            return OrderCheckResult(False, "first_live_order_not_confirmed")
        return None

    def check_order(
        self,
        side: Signal,
        proposed_pool_fraction: float = 0.0,
        is_first_live_order: bool = False,
    ) -> OrderCheckResult:
        """Bayrak kontrolleri + tek emir orani (eski arayuz). Yeni kod
        check_buy'i kullanir (toplam pozisyon tavani dahil)."""
        if side is Signal.SELL:
            return OrderCheckResult(True, "sell_always_allowed", approved_capital_fraction=1.0)
        blocked = self._flag_checks(is_first_live_order)
        if blocked is not None:
            return blocked
        clamped_pool_fraction = max(0.0, min(1.0, proposed_pool_fraction))
        return OrderCheckResult(
            True, "ok", approved_capital_fraction=clamped_pool_fraction * self.total_capital_allocation_pct
        )

    def check_buy(
        self, snapshot: AccountSnapshot, desired_notional: float, is_first_live_order: bool = False
    ) -> OrderCheckResult:
        blocked = self._flag_checks(is_first_live_order)
        if blocked is not None:
            return blocked
        equity = snapshot.equity
        if not equity > 0:
            return OrderCheckResult(False, f"non_positive_equity ({equity})")
        cap = self.total_capital_allocation_pct * equity
        headroom = cap - snapshot.market_value
        approved = min(desired_notional, headroom, snapshot.cash)
        if approved < self.min_notional:
            if headroom < self.min_notional:
                reason = f"exposure_cap_reached (acik {snapshot.market_value:.2f} / tavan {cap:.2f})"
            elif snapshot.cash < self.min_notional:
                reason = f"insufficient_cash ({snapshot.cash:.2f})"
            else:
                reason = f"below_min_notional ({approved:.2f} < {self.min_notional})"
            return OrderCheckResult(False, reason)
        reason = "ok" if approved >= desired_notional - 1e-9 else "ok_capped"
        return OrderCheckResult(True, reason, approved_capital_fraction=approved / equity, approved_notional=approved)

    def check_and_maybe_trip_daily_loss(self, pnl_pct_today: float) -> bool:
        """Gunluk zarar limiti (gerceklesen + gerceklesmemis) asildiysa
        kill-switch'i tetikler. Idempotent."""
        if pnl_pct_today <= -self.max_daily_loss_pct:
            if not self.state.is_kill_switch_tripped():
                self.state.trip_kill_switch(
                    reason=f"gunluk P&L {pnl_pct_today:.4%} <= -{self.max_daily_loss_pct:.4%}"
                )
            return True
        return False
