from datetime import UTC, date, datetime

import pytest

from tradecrm.broker.base import BrokerError
from tradecrm.broker.paper_adapter import PaperBrokerAdapter
from tradecrm.db.models import Trade
from tradecrm.db.repository import TradeRepository
from tradecrm.strategy.base import Signal


class _FakePriceSource:
    def __init__(self, price: float):
        self.prices = {}
        self.price = price

    def fetch_latest_price(self, symbol: str) -> float:
        return self.prices.get(symbol, self.price)


@pytest.fixture()
def price_source():
    return _FakePriceSource(price=100.0)


@pytest.fixture()
def adapter(db_session, price_source):
    return PaperBrokerAdapter(db_session, price_source, initial_capital=10_000.0, fee_rate=0.001)


def buy(adapter, symbol="BTC/USDT", notional=5_000.0, coid=None):
    coid = coid or f"buy-{symbol}-{notional}"
    return adapter.submit_order(coid, symbol, Signal.BUY, notional, strategy_name="s")


def sell(adapter, symbol="BTC/USDT", coid=None, reason=None):
    return adapter.submit_order(coid or f"sell-{symbol}", symbol, Signal.SELL, None, strategy_name="s", reason=reason)


class TestBuy:
    def test_buy_opens_position_and_computes_qty(self, adapter):
        result = buy(adapter, notional=5_000.0)
        assert result.executed is True
        assert result.price == 100.0
        assert result.qty == pytest.approx((5_000.0 - 5.0) / 100.0)

    def test_buy_blocked_when_position_already_open(self, adapter):
        buy(adapter, coid="a")
        result = buy(adapter, coid="b")
        assert result.executed is False
        assert result.reason == "position_already_open"

    def test_zero_notional_rejected(self, adapter):
        assert buy(adapter, notional=0.0).reason == "zero_notional"

    def test_cannot_spend_more_than_cash(self, adapter):
        assert buy(adapter, notional=10_000.01).reason == "insufficient_cash"

    def test_different_symbols_can_have_concurrent_positions(self, adapter):
        assert buy(adapter, "BTC/USDT", 3_000.0).executed
        assert buy(adapter, "ETH/USDT", 3_000.0).executed

    def test_invalid_price_raises(self, adapter, price_source):
        price_source.price = 0.0
        with pytest.raises(BrokerError):
            buy(adapter)


class TestAccounting:
    def test_cash_allocated_equity_are_separate(self, adapter, price_source):
        buy(adapter, "BTC/USDT", 4_000.0)
        buy(adapter, "ETH/USDT", 2_000.0)
        price_source.prices = {"BTC/USDT": 110.0, "ETH/USDT": 90.0}

        snap = adapter.get_snapshot()
        assert snap.cash == pytest.approx(4_000.0)  # 10k - 4k - 2k (komisyon maliyete dahil)
        assert snap.allocated == pytest.approx(6_000.0)
        btc_qty = (4_000.0 - 4.0) / 100.0
        eth_qty = (2_000.0 - 2.0) / 100.0
        assert snap.market_value == pytest.approx(btc_qty * 110.0 + eth_qty * 90.0)
        assert snap.equity == pytest.approx(snap.cash + snap.market_value)
        assert snap.realized_equity == pytest.approx(10_000.0)
        assert len(snap.positions) == 2

    def test_second_buy_limited_by_remaining_cash(self, adapter):
        buy(adapter, "BTC/USDT", 7_000.0)
        assert buy(adapter, "ETH/USDT", 3_500.0).reason == "insufficient_cash"
        assert buy(adapter, "ETH/USDT", 3_000.0).executed

    def test_flat_round_trip_loses_both_fees(self, adapter, db_session):
        buy(adapter, notional=10_000.0)
        result = sell(adapter, reason="stop_loss")
        trade = db_session.get(Trade, result.trade_id)

        entry_fee = 10.0
        qty = (10_000.0 - entry_fee) / 100.0
        exit_fee = qty * 100.0 * 0.001
        assert trade.entry_fees == pytest.approx(entry_fee)
        assert trade.fees == pytest.approx(entry_fee + exit_fee)  # alis + satis birlikte
        assert trade.realized_pnl == pytest.approx(-(entry_fee + exit_fee))
        assert adapter.get_snapshot().cash == pytest.approx(10_000.0 - entry_fee - exit_fee)
        assert trade.exit_reason == "stop_loss"

    def test_profitable_round_trip(self, adapter, price_source):
        buy(adapter, notional=10_000.0)
        price_source.price = 110.0
        sell(adapter)
        snap = adapter.get_snapshot()
        assert snap.allocated == 0 and snap.market_value == 0
        assert snap.cash > 10_000.0


class TestSell:
    def test_sell_without_open_position_rejected(self, adapter):
        assert sell(adapter).reason == "no_open_position"

    def test_sell_closes_position(self, adapter, price_source):
        buy(adapter)
        price_source.price = 120.0
        assert sell(adapter).executed is True
        assert adapter.get_open_position("BTC/USDT") is None

    def test_hold_signal_is_no_action(self, adapter):
        result = adapter.submit_order("h", "BTC/USDT", Signal.HOLD, 100.0, strategy_name="s")
        assert result.reason == "hold_no_action"


class TestIdempotency:
    def test_same_client_order_id_does_not_fill_twice(self, adapter, db_session):
        first = buy(adapter, coid="tc-1")
        again = buy(adapter, coid="tc-1")
        assert again.trade_id == first.trade_id
        assert len(TradeRepository(db_session).get_open_trades(broker="paper")) == 1

    def test_get_order_finds_entry_and_exit(self, adapter):
        assert adapter.get_order("tc-x") is None
        b = buy(adapter, coid="tc-buy")
        s = sell(adapter, coid="tc-sell")
        assert adapter.get_order("tc-buy").trade_id == b.trade_id
        assert adapter.get_order("tc-sell").reason == "closed"
        assert adapter.get_order("tc-sell").trade_id == s.trade_id


class TestIsolation:
    def test_other_broker_positions_ignored(self, adapter, db_session):
        TradeRepository(db_session).open_trade(
            symbol="BTC/USDT", strategy_name="s", broker="binance", entry_time=datetime.now(UTC),
            entry_price=100.0, qty=1.0, account_balance_at_entry=1000.0,
        )
        assert adapter.get_open_position("BTC/USDT") is None
        assert adapter.get_snapshot().cash == pytest.approx(10_000.0)

    def test_sub_accounts_are_isolated_and_prefix_filter_sees_all(self, db_session, price_source):
        from tradecrm.risk.state import RiskStateStore

        a = PaperBrokerAdapter(db_session, price_source, initial_capital=6_000.0, account="trend")
        b = PaperBrokerAdapter(db_session, price_source, initial_capital=4_000.0, account="mr")
        assert a.name == "paper:trend" and b.name == "paper:mr"
        assert buy(a, notional=3_000.0, coid="a1").executed
        assert b.get_open_position("BTC/USDT") is None
        assert buy(b, notional=2_000.0, coid="b1").executed
        assert a.get_snapshot().cash == pytest.approx(3_000.0)
        assert b.get_snapshot().cash == pytest.approx(2_000.0)

        assert len(RiskStateStore(db_session, broker="paper:trend").get_open_trades()) == 1
        assert len(RiskStateStore(db_session, broker="paper*").get_open_trades()) == 2


def test_round_trip_writes_closed_trade(adapter, db_session, price_source):
    buy(adapter, notional=4_000.0)
    price_source.price = 105.0
    sell(adapter)
    closed = TradeRepository(db_session).get_daily_report(date(2000, 1, 1), date(2100, 1, 1))
    assert sum(row.trade_count for row in closed) == 1
