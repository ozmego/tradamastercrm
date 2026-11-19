from datetime import UTC, datetime
from typing import Protocol

from sqlmodel import Session, or_, select

from tradecrm.broker.base import (
    AccountSnapshot,
    BrokerAdapter,
    BrokerError,
    OrderResult,
    PositionView,
)
from tradecrm.db.models import Trade
from tradecrm.db.repository import TradeRepository
from tradecrm.strategy.base import Signal


class PriceSource(Protocol):
    def fetch_latest_price(self, symbol: str) -> float: ...


class PaperBrokerAdapter(BrokerAdapter):
    """Simule fill'ler uretir, gercek para hareket ettirmez. Fill'leri canli
    adapter'larla ayni sekilde trades tablosuna yazar.

    Muhasebe (tek kaynak Trade tablosu):
      cash      = initial + kapali islemlerin gerceklesen P&L'i - acik islemlerin maliyeti
      allocated = acik islemlerin maliyeti (entry_price*qty + entry_fees)
      equity    = cash + acik pozisyonlarin piyasa degeri

    "Borsa" bu DB'dir: get_order, Trade satirlarindaki client_order_id
    alanlarina bakar; ayni client_order_id ile ikinci submit ikinci bir
    fill uretmez."""

    name = "paper"

    def __init__(
        self,
        session: Session,
        price_source: PriceSource,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.001,
        account: str | None = None,
    ) -> None:
        # account verilirse broker adi "paper:<account>" olur: portfoyde her
        # profil kendi alt hesabinda (ayri bakiye ve pozisyon) calisir.
        if account:
            self.name = f"paper:{account}"
        self.session = session
        self.price_source = price_source
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.trades = TradeRepository(session)

    # ------------------------------------------------------------- hesap
    def _cost(self, t: Trade) -> float:
        return t.entry_price * t.qty + (t.entry_fees or 0.0)

    def _cash_and_open(self) -> tuple[float, list[Trade]]:
        closed = self.session.exec(select(Trade).where(Trade.broker == self.name, Trade.status == "CLOSED"))
        realized = sum(t.realized_pnl or 0.0 for t in closed)
        open_trades = self.trades.get_open_trades(broker=self.name)
        cash = self.initial_capital + realized - sum(self._cost(t) for t in open_trades)
        return cash, open_trades

    def get_snapshot(self) -> AccountSnapshot:
        cash, open_trades = self._cash_and_open()
        positions = []
        for t in open_trades:
            price = float(self.price_source.fetch_latest_price(t.symbol))
            if not price > 0:
                raise BrokerError(f"gecersiz fiyat {t.symbol}: {price}")
            positions.append(PositionView(t.symbol, t.qty, self._cost(t), price, t.qty * price, t.id))
        return AccountSnapshot(
            cash=cash,
            allocated=sum(p.cost for p in positions),
            market_value=sum(p.market_value for p in positions),
            positions=positions,
        )

    def get_balance(self) -> float:
        """Gerceklesen equity (nakit + maliyet); fiyat gerektirmez."""
        cash, open_trades = self._cash_and_open()
        return cash + sum(self._cost(t) for t in open_trades)

    def get_open_position(self, symbol: str) -> Trade | None:
        open_trades = self.trades.get_open_trades(symbol=symbol, broker=self.name)
        return open_trades[0] if open_trades else None

    # -------------------------------------------------------------- emirler
    def get_order(self, client_order_id: str) -> OrderResult | None:
        trade = self.session.exec(
            select(Trade).where(
                Trade.broker == self.name,
                or_(Trade.entry_client_order_id == client_order_id, Trade.exit_client_order_id == client_order_id),
            )
        ).first()
        if trade is None:
            return None
        if trade.entry_client_order_id == client_order_id:
            return OrderResult(True, "filled", trade.id, trade.entry_price, trade.qty, client_order_id)
        return OrderResult(True, "closed", trade.id, trade.exit_price, trade.qty, client_order_id)

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: Signal,
        notional: float | None,
        strategy_name: str,
        reason: str | None = None,
    ) -> OrderResult:
        if side is Signal.HOLD:
            return OrderResult(False, "hold_no_action")
        existing = self.get_order(client_order_id)
        if existing is not None:
            return existing  # idempotent: ayni kimlik ikinci fill uretmez

        price = float(self.price_source.fetch_latest_price(symbol))
        if not price > 0:
            raise BrokerError(f"gecersiz fiyat {symbol}: {price}")
        now = datetime.now(UTC)

        if side is Signal.BUY:
            if self.get_open_position(symbol) is not None:
                return OrderResult(False, "position_already_open", client_order_id=client_order_id)
            if not notional or notional <= 0:
                return OrderResult(False, "zero_notional", client_order_id=client_order_id)
            cash, _ = self._cash_and_open()
            if notional > cash + 1e-9:
                return OrderResult(False, "insufficient_cash", client_order_id=client_order_id)
            fees = notional * self.fee_rate
            qty = (notional - fees) / price
            trade = self.trades.open_trade(
                symbol=symbol,
                strategy_name=strategy_name,
                broker=self.name,
                entry_time=now,
                entry_price=price,
                qty=qty,
                account_balance_at_entry=self.get_balance(),
                signal_reason=reason,
                entry_fees=fees,
                entry_client_order_id=client_order_id,
            )
            return OrderResult(True, "filled", trade.id, price, qty, client_order_id)

        existing_pos = self.get_open_position(symbol)
        if existing_pos is None:
            return OrderResult(False, "no_open_position", client_order_id=client_order_id)
        fees = existing_pos.qty * price * self.fee_rate
        closed = self.trades.close_trade(
            existing_pos.id, exit_time=now, exit_price=price, fees=fees, exit_reason=reason,
            exit_client_order_id=client_order_id,
        )
        return OrderResult(True, "closed", closed.id, price, closed.qty, client_order_id)
