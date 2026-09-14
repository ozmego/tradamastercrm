"""Risk durumunun (kill-switch, TRADING_ENABLED, gunluk P&L) gecici
in-memory implementasyonu.

Faz 5'te bu sinifin disaridan gorulen arayuzu (RiskStateStore) aynen
korunacak, ancak icerideki depolama db/repository.py'ye (RiskEvent
tablosu + trades agregasyonu) baglanacak sekilde degistirilecek --
boylece RiskManager ve target_sizing kodunda degisiklik gerekmeyecek.
"""

from dataclasses import dataclass
from datetime import date


@dataclass
class _DailyState:
    realized_pnl_pct: float = 0.0


class RiskStateStore:
    def __init__(self) -> None:
        self._trading_enabled = False
        self._kill_switch_tripped = False
        self._kill_switch_reason: str = ""
        self._first_order_confirmed = False
        self._daily: dict[date, _DailyState] = {}

    # --- TRADING_ENABLED -------------------------------------------------
    def is_trading_enabled(self) -> bool:
        return self._trading_enabled

    def set_trading_enabled(self, value: bool) -> None:
        self._trading_enabled = value

    # --- Kill switch -------------------------------------------------------
    def is_kill_switch_tripped(self) -> bool:
        return self._kill_switch_tripped

    def trip_kill_switch(self, reason: str) -> None:
        self._kill_switch_tripped = True
        self._kill_switch_reason = reason

    def reset_kill_switch(self) -> None:
        self._kill_switch_tripped = False
        self._kill_switch_reason = ""

    @property
    def kill_switch_reason(self) -> str:
        return self._kill_switch_reason

    # --- Ilk canli emir onayi ----------------------------------------------
    def is_first_order_confirmed(self) -> bool:
        return self._first_order_confirmed

    def confirm_first_order(self) -> None:
        self._first_order_confirmed = True

    # --- Gunluk gerceklesen P&L ---------------------------------------------
    def get_realized_pnl_pct(self, for_date: date) -> float:
        return self._daily.get(for_date, _DailyState()).realized_pnl_pct

    def record_realized_pnl_pct(self, for_date: date, pnl_pct_delta: float) -> None:
        state = self._daily.setdefault(for_date, _DailyState())
        state.realized_pnl_pct += pnl_pct_delta

    def set_realized_pnl_pct(self, for_date: date, pnl_pct: float) -> None:
        self._daily[for_date] = _DailyState(realized_pnl_pct=pnl_pct)
