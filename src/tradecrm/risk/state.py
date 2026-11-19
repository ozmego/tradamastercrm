"""RiskManager/runner'in kullandigi, HESAP BAZLI risk-durumu arayuzu.

Tek kaynak veritabanidir (db.repository): kill-switch, TRADING_ENABLED
ve ilk-canli-emir onayi RiskEvent satirlari (hesap etiketli), P&L ise
Trade tablosundan (broker = hesap) turetilir.

Hesap adlari: "paper", "paper:<profil>" (portfoy alt hesabi), "binance".
Paper ve canli durumlari hicbir zaman karismaz: her sorgu hesaba gore
filtrelenir. "paper*" deseni (dashboard/rapor) tum paper alt hesaplarini
okur; risk bayraklari icin kok hesap ("paper") kullanilir.
"""

from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session

from tradecrm.db.repository import RiskEventRepository, TradeRepository


class RiskStateStore:
    def __init__(self, session: Session, broker: str = "paper") -> None:
        self.session = session
        self.broker = broker  # Trade filtresi (desen olabilir: "paper*")
        self.account = broker.rstrip("*")  # risk bayraklari icin hesap
        self._risk_events = RiskEventRepository(session, account=self.account)
        self._trades = TradeRepository(session)

    @property
    def is_live(self) -> bool:
        return not self.account.startswith("paper")

    # --- TRADING_ENABLED -----------------------------------------------------
    def is_trading_enabled(self) -> bool:
        return self._risk_events.is_trading_enabled()

    def set_trading_enabled(self, value: bool, reason: str | None = None) -> None:
        self._risk_events.record_event("TRADING_ENABLED" if value else "TRADING_DISABLED", reason=reason)

    def last_trading_enabled_time(self):
        """Hesabin ya da kokunun alim izninin en son acildigi an (drift penceresi)."""
        return self._risk_events.last_event_time(("TRADING_ENABLED",))

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
        return self._trades.get_realized_pnl_pct_for_date(for_date, broker=self.broker)

    def get_closed_trades_between(self, start, end):
        return self._trades.get_closed_trades_between(start, end, broker=self.broker)

    def get_closed_trades_since_last_reset(self):
        """Devre kesiciler icin: son KILL_SWITCH_RESET'ten bu yana kapanan
        islemler (reset = insanin bilincli "temiz sayfa" karari)."""
        reset = self._risk_events.last_reset_time()
        start = reset if reset is not None else datetime(1970, 1, 1, tzinfo=UTC)
        return self.get_closed_trades_between(start, datetime.now(UTC) + timedelta(seconds=1))

    def get_all_closed_trades(self):
        return self._trades.get_all_closed_trades(broker=self.broker)

    def get_open_trades(self, symbol: str | None = None):
        return self._trades.get_open_trades(symbol=symbol, broker=self.broker)

    def update_exit_levels(self, trade_id, **levels):
        return self._trades.update_exit_levels(trade_id, **levels)
