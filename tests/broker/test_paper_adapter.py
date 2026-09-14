from datetime import date

import pytest

from tradecrm.broker.paper_adapter import PaperBrokerAdapter
from tradecrm.strategy.base import Signal


class _FakePriceSource:
    def __init__(self, price: float):
        self.price = price

    def fetch_latest_price(self, symbol: str) -> float:
        return self.price


@pytest.fixture()
def price_source():
    return _FakePriceSource(price=100.0)


@pytest.fixture()
def adapter(db_session, price_source):
    return PaperBrokerAdapter(db_session, price_source, initial_capital=10_000.0, fee_rate=0.001)


class TestBuy:
    def test_buy_opens_position_and_computes_qty(self, adapter, price_source):
        result = adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.5, strategy_name="s")

        assert result.executed is True
        assert result.price == 100.0
        notional = 10_000.0 * 0.5
        fees = notional * 0.001
        expected_qty = (notional - fees) / 100.0
        assert result.qty == pytest.approx(expected_qty)

    def test_buy_blocked_when_position_already_open(self, adapter):
        adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.5, strategy_name="s")
        result = adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.5, strategy_name="s")
        assert result.executed is False
        assert result.reason == "position_already_open"

    def test_buy_zero_fraction_rejected(self, adapter):
        result = adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.0, strategy_name="s")
        assert result.executed is False
        assert result.reason == "zero_capital_fraction"

    def test_buy_oversized_fraction_clamped_to_one(self, adapter):
        result = adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=5.0, strategy_name="s")
        notional = 10_000.0 * 1.0
        fees = notional * 0.001
        expected_qty = (notional - fees) / 100.0
        assert result.qty == pytest.approx(expected_qty)

    def test_different_symbols_can_have_concurrent_positions(self, adapter):
        r1 = adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.3, strategy_name="s")
        r2 = adapter.place_order("ETH/USDT", Signal.BUY, capital_fraction=0.3, strategy_name="s")
        assert r1.executed is True
        assert r2.executed is True


class TestSell:
    def test_sell_without_open_position_rejected(self, adapter):
        result = adapter.place_order("BTC/USDT", Signal.SELL, capital_fraction=0.0, strategy_name="s")
        assert result.executed is False
        assert result.reason == "no_open_position"

    def test_sell_closes_position_with_profit(self, adapter, price_source):
        adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.5, strategy_name="s")
        price_source.price = 120.0  # %20 artis

        result = adapter.place_order("BTC/USDT", Signal.SELL, capital_fraction=0.0, strategy_name="s")

        assert result.executed is True
        assert adapter.get_open_position("BTC/USDT") is None

    def test_balance_reflects_realized_pnl_after_round_trip(self, adapter, price_source):
        starting_balance = adapter.get_balance()
        adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=1.0, strategy_name="s")
        price_source.price = 110.0  # %10 artis
        adapter.place_order("BTC/USDT", Signal.SELL, capital_fraction=0.0, strategy_name="s")

        new_balance = adapter.get_balance()
        assert new_balance > starting_balance

    def test_hold_signal_is_no_action(self, adapter):
        result = adapter.place_order("BTC/USDT", Signal.HOLD, capital_fraction=0.5, strategy_name="s")
        assert result.executed is False
        assert result.reason == "hold_no_action"


class TestRoundTripPipeline:
    def test_buy_then_sell_writes_closed_trade_to_db(self, adapter, db_session, price_source):
        """Ucdan uca boru hattinin saglik kontrolu: data(price_source) ->
        broker(paper) -> db(trades). Binance kimlik bilgilerine dokunulmaz."""
        from tradecrm.db.repository import TradeRepository

        adapter.place_order("BTC/USDT", Signal.BUY, capital_fraction=0.4, strategy_name="ema_rsi_crossover")
        price_source.price = 105.0
        adapter.place_order("BTC/USDT", Signal.SELL, capital_fraction=0.0, strategy_name="ema_rsi_crossover")

        repo = TradeRepository(db_session)
        closed = repo.get_daily_report(date(2000, 1, 1), date(2100, 1, 1))
        assert sum(row.trade_count for row in closed) == 1
