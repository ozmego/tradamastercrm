"""RiskManager/target_sizing'in kullandigi risk-durumu arayuzu.

Faz 5 itibariyla tek kaynak veritabanidir (db.repository): kill-switch,
TRADING_ENABLED ve ilk-canli-emir onayi RiskEvent satirlari olarak, gunluk
gerceklesen P&L ise Trade tablosundan turetilir. Ayri bir in-memory/dosya
state'i yoktur -- boylece RiskManager, dashboard ve runner hep ayni veriyi
gorur.
"""

from datetime import date

from sqlmodel import Session

from tradecrm.db.repository import RiskEventRepository, TradeRepository


class RiskStateStore:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._risk_events = RiskEventRepository(session)
        self._trades = TradeRepository(session)

    # --- TRADING_ENABLED -----------------------------------------------------
    def is_trading_enabled(self) -> bool:
        return self._risk_events.is_trading_enabled()

    def set_trading_enabled(self, value: bool) -> None:
        self._risk_events.record_event("TRADING_ENABLED" if value else "TRADING_DISABLED")

    # --- Kill switch -----------------------------------------------------------
    def is_kill_switch_tripped(self) -> bool:
        return self._risk_events.is_kill_switch_tripped()

    def trip_kill_switch(self, reason: str) -> None:
        self._risk_events.record_event("KILL_SWITCH_TRIP", reason=reason)

    def reset_kill_switch(self) -> None:
        self._risk_events.record_event("KILL_SWITCH_RESET")

    @property
    def kill_switch_reason(self) -> str:
        return self._risk_events.get_kill_switch_reason()

    # --- Ilk canli emir onayi ----------------------------------------------------
    def is_first_order_confirmed(self) -> bool:
        return self._risk_events.is_first_order_confirmed()

    def confirm_first_order(self) -> None:
        self._risk_events.record_event("FIRST_ORDER_CONFIRMED")

    # --- Gunluk gerceklesen P&L (Trade tablosundan turetilir) ---------------------
    def get_realized_pnl_pct(self, for_date: date) -> float:
        return self._trades.get_realized_pnl_pct_for_date(for_date)
