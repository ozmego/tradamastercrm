import pandas as pd
import pytest

from tradecrm.broker.base import OrderResult
from tradecrm.risk.guardrails import OrderCheckResult
from tradecrm.runner.live_runner import LiveRunner
from tradecrm.strategy.base import Signal


class _StubDataProvider:
    def __init__(self, should_raise: bool = False):
        self.should_raise = should_raise

    def fetch_latest_ohlcv(self, symbol, timeframe, limit):
        if self.should_raise:
            raise ConnectionError("borsa verisi cekilemedi")
        return pd.DataFrame({"Close": [100.0] * 5})


class _StubStrategy:
    name = "stub_strategy"

    def __init__(self, signal: Signal):
        self._signal = signal

    def generate_signal(self, df):
        return self._signal


class _StubState:
    def __init__(self, realized_pnl_pct_today: float = 0.0):
        self._pnl = realized_pnl_pct_today

    def get_realized_pnl_pct(self, for_date):
        return self._pnl

    def is_kill_switch_tripped(self):
        return False


class _StubRiskManager:
    def __init__(self, allowed=True, reason="ok", approved_capital_fraction=0.3):
        self.allowed = allowed
        self.reason = reason
        self.approved_capital_fraction = approved_capital_fraction
        self.check_order_calls: list = []
        self.daily_loss_calls: list = []

    def check_and_maybe_trip_daily_loss(self, pct):
        self.daily_loss_calls.append(pct)
        return False

    def check_order(self, side, proposed_pool_fraction=0.0, is_first_live_order=False):
        self.check_order_calls.append((side, proposed_pool_fraction, is_first_live_order))
        return OrderCheckResult(self.allowed, self.reason, approved_capital_fraction=self.approved_capital_fraction)


class _StubBroker:
    name = "stub_broker"

    def __init__(self, executed=True, reason="filled", price=100.0, qty=1.0, trade_id=1):
        self.executed = executed
        self.reason = reason
        self.price = price
        self.qty = qty
        self.trade_id = trade_id
        self.calls: list = []

    def place_order(self, symbol, side, capital_fraction, strategy_name, signal_reason=None):
        self.calls.append((symbol, side, capital_fraction, strategy_name, signal_reason))
        return OrderResult(self.executed, self.reason, trade_id=self.trade_id, price=self.price, qty=self.qty)


class _StubNotifier:
    def __init__(self):
        self.messages: list = []

    def send_message(self, text):
        self.messages.append(text)
        return True


class _StubAuditLogger:
    def __init__(self):
        self.entries: list = []

    def log(self, event_type, **fields):
        self.entries.append((event_type, fields))


def _make_runner(strategy_signal, risk_manager=None, broker=None, data_provider=None, **overrides):
    defaults = dict(
        data_provider=data_provider or _StubDataProvider(),
        strategy=_StubStrategy(strategy_signal),
        risk_manager=risk_manager or _StubRiskManager(),
        state=_StubState(),
        broker=broker or _StubBroker(),
        notifier=_StubNotifier(),
        audit_logger=_StubAuditLogger(),
        symbol="BTC/USDT",
        timeframe="1h",
        daily_growth_target_pct=0.0075,
        min_position_fraction=0.2,
        max_position_fraction=1.0,
    )
    defaults.update(overrides)
    return LiveRunner(**defaults)


class TestHoldSignal:
    def test_hold_does_not_call_risk_or_broker(self):
        risk_manager = _StubRiskManager()
        broker = _StubBroker()
        runner = _make_runner(Signal.HOLD, risk_manager=risk_manager, broker=broker)

        runner.run_once()

        assert risk_manager.check_order_calls == []
        assert broker.calls == []
        assert runner.notifier.messages == []

    def test_hold_still_logs_signal_evaluated(self):
        runner = _make_runner(Signal.HOLD)
        runner.run_once()
        event_types = [e for e, _ in runner.audit_logger.entries]
        assert event_types == ["signal_evaluated"]

    def test_daily_loss_check_runs_even_on_hold(self):
        risk_manager = _StubRiskManager()
        runner = _make_runner(Signal.HOLD, risk_manager=risk_manager)
        runner.run_once()
        assert risk_manager.daily_loss_calls == [0.0]


class TestBuySignalAllowed:
    def test_buy_allowed_calls_broker_with_approved_fraction(self):
        risk_manager = _StubRiskManager(allowed=True, approved_capital_fraction=0.375)
        broker = _StubBroker()
        runner = _make_runner(Signal.BUY, risk_manager=risk_manager, broker=broker)

        runner.run_once()

        assert len(broker.calls) == 1
        symbol, side, capital_fraction, strategy_name, _reason = broker.calls[0]
        assert side is Signal.BUY
        assert capital_fraction == 0.375

    def test_buy_allowed_sends_notification_on_execution(self):
        runner = _make_runner(Signal.BUY, broker=_StubBroker(executed=True))
        runner.run_once()
        assert len(runner.notifier.messages) == 1

    def test_buy_allowed_but_not_executed_sends_no_notification(self):
        runner = _make_runner(Signal.BUY, broker=_StubBroker(executed=False, reason="position_already_open"))
        runner.run_once()
        assert runner.notifier.messages == []

    def test_audit_logs_signal_and_order_result_not_blocked(self):
        runner = _make_runner(Signal.BUY)
        runner.run_once()
        event_types = [e for e, _ in runner.audit_logger.entries]
        assert event_types == ["signal_evaluated", "order_result"]


class TestBuySignalBlocked:
    def test_blocked_does_not_call_broker(self):
        risk_manager = _StubRiskManager(allowed=False, reason="trading_disabled")
        broker = _StubBroker()
        runner = _make_runner(Signal.BUY, risk_manager=risk_manager, broker=broker)

        runner.run_once()

        assert broker.calls == []
        assert runner.notifier.messages == []

    def test_audit_logs_order_blocked_with_reason(self):
        risk_manager = _StubRiskManager(allowed=False, reason="trading_disabled")
        runner = _make_runner(Signal.BUY, risk_manager=risk_manager)
        runner.run_once()
        event_types = [e for e, _ in runner.audit_logger.entries]
        assert event_types == ["signal_evaluated", "order_blocked"]
        _, fields = runner.audit_logger.entries[-1]
        assert fields["reason"] == "trading_disabled"


class TestSafeTick:
    def test_run_once_propagates_exceptions(self):
        runner = _make_runner(Signal.HOLD, data_provider=_StubDataProvider(should_raise=True))
        with pytest.raises(ConnectionError):
            runner.run_once()

    def test_safe_tick_catches_exception_and_notifies(self):
        runner = _make_runner(Signal.HOLD, data_provider=_StubDataProvider(should_raise=True))
        runner.safe_tick()  # raise etmemeli

        assert len(runner.notifier.messages) == 1
        assert "HATA" in runner.notifier.messages[0]
        event_types = [e for e, _ in runner.audit_logger.entries]
        assert "tick_error" in event_types
