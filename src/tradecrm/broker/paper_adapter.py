from datetime import datetime, timezone
from typing import Protocol

from sqlmodel import Session, select

from tradecrm.broker.base import BrokerAdapter, OrderResult
from tradecrm.db.models import Trade
from tradecrm.db.repository import TradeRepository
from tradecrm.strategy.base import Signal


class PriceSource(Protocol):
    def fetch_latest_price(self, symbol: str) -> float: ...


class PaperBrokerAdapter(BrokerAdapter):
    """Simule fill'ler uretir, gercek para hareket ettirmez. Fill'leri
    diger adapter'larla ayni sekilde db/repository.py uzerinden trades
    tablosuna yazar -- boylece dashboard/risk/target_sizing paper ve
    canli veriyi ayni yerden okur.

    Bakiye ayri bir tabloda tutulmaz: initial_capital + kapali paper
    trade'lerin gerceklesen P&L toplami olarak hesaplanir (tek kaynak
    Trade tablosu)."""

    name = "paper"

    def __init__(
        self,
        session: Session,
        price_source: PriceSource,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.001,
    ) -> None:
        self.session = session
        self.price_source = price_source
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.trades = TradeRepository(session)

    def get_balance(self) -> float:
        stmt = select(Trade).where(Trade.broker == self.name, Trade.status == "CLOSED")
        realized = sum(t.realized_pnl or 0.0 for t in self.session.exec(stmt))
        return self.initial_capital + realized

    def get_open_position(self, symbol: str) -> Trade | None:
        open_trades = self.trades.get_open_trades(symbol=symbol)
        paper_trades = [t for t in open_trades if t.broker == self.name]
        return paper_trades[0] if paper_trades else None

    def place_order(
        self,
        symbol: str,
        side: Signal,
        capital_fraction: float,
        strategy_name: str,
        signal_reason: str | None = None,
    ) -> OrderResult:
        if side is Signal.HOLD:
            return OrderResult(False, "hold_no_action")

        price = self.price_source.fetch_latest_price(symbol)
        now = datetime.now(timezone.utc)

        if side is Signal.BUY:
            if self.get_open_position(symbol) is not None:
                return OrderResult(False, "position_already_open")

            balance = self.get_balance()
            notional = balance * max(0.0, min(1.0, capital_fraction))
            if notional <= 0:
                return OrderResult(False, "zero_capital_fraction")

            fees = notional * self.fee_rate
            qty = (notional - fees) / price
            trade = self.trades.open_trade(
                symbol=symbol,
                strategy_name=strategy_name,
                broker=self.name,
                entry_time=now,
                entry_price=price,
                qty=qty,
                account_balance_at_entry=balance,
                signal_reason=signal_reason,
            )
            return OrderResult(True, "filled", trade_id=trade.id, price=price, qty=qty)

        # side is Signal.SELL
        existing = self.get_open_position(symbol)
        if existing is None:
            return OrderResult(False, "no_open_position")

        notional = existing.qty * price
        fees = notional * self.fee_rate
        closed = self.trades.close_trade(existing.id, exit_time=now, exit_price=price, fees=fees)
        return OrderResult(True, "closed", trade_id=closed.id, price=price, qty=closed.qty)
