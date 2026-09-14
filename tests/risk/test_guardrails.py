import pytest

from tradecrm.risk.guardrails import RiskManager
from tradecrm.risk.state import RiskStateStore
from tradecrm.strategy.base import Signal


@pytest.fixture()
def state() -> RiskStateStore:
    return RiskStateStore()


@pytest.fixture()
def risk_manager(state: RiskStateStore) -> RiskManager:
    return RiskManager(state, total_capital_allocation_pct=0.75, max_daily_loss_pct=0.05)


class TestCheckOrderBuy:
    def test_blocked_when_trading_disabled(self, risk_manager, state):
        state.set_trading_enabled(False)
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=0.5)
        assert result.allowed is False
        assert result.reason == "trading_disabled"

    def test_blocked_when_kill_switch_tripped(self, risk_manager, state):
        state.set_trading_enabled(True)
        state.trip_kill_switch("test")
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=0.5)
        assert result.allowed is False
        assert "kill_switch_tripped" in result.reason

    def test_blocked_when_first_order_not_confirmed(self, risk_manager, state):
        state.set_trading_enabled(True)
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=0.5, is_first_live_order=True)
        assert result.allowed is False
        assert result.reason == "first_live_order_not_confirmed"

    def test_allowed_when_first_order_confirmed(self, risk_manager, state):
        state.set_trading_enabled(True)
        state.confirm_first_order()
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=0.5, is_first_live_order=True)
        assert result.allowed is True

    def test_approved_fraction_is_pool_fraction_times_75pct(self, risk_manager, state):
        state.set_trading_enabled(True)
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=0.4)
        assert result.approved_capital_fraction == pytest.approx(0.4 * 0.75)

    def test_oversized_pool_fraction_is_clamped_independently(self, risk_manager, state):
        """target_sizing hatali/asiri bir deger uretse bile (orn. 1.5),
        RiskManager toplam sermayenin %75'ini asan bir onay vermemeli."""
        state.set_trading_enabled(True)
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=1.5)
        assert result.approved_capital_fraction == pytest.approx(1.0 * 0.75)

    def test_negative_pool_fraction_clamped_to_zero(self, risk_manager, state):
        state.set_trading_enabled(True)
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=-0.3)
        assert result.approved_capital_fraction == 0.0


class TestCheckOrderSell:
    def test_sell_allowed_even_when_trading_disabled(self, risk_manager, state):
        state.set_trading_enabled(False)
        result = risk_manager.check_order(Signal.SELL)
        assert result.allowed is True

    def test_sell_allowed_even_when_kill_switch_tripped(self, risk_manager, state):
        state.set_trading_enabled(True)
        state.trip_kill_switch("test")
        result = risk_manager.check_order(Signal.SELL)
        assert result.allowed is True

    def test_sell_allowed_without_first_order_confirmation(self, risk_manager, state):
        result = risk_manager.check_order(Signal.SELL, is_first_live_order=True)
        assert result.allowed is True


class TestDailyLossKillSwitch:
    def test_trips_when_loss_exceeds_threshold(self, risk_manager, state):
        tripped = risk_manager.check_and_maybe_trip_daily_loss(-0.06)
        assert tripped is True
        assert state.is_kill_switch_tripped() is True

    def test_does_not_trip_within_threshold(self, risk_manager, state):
        tripped = risk_manager.check_and_maybe_trip_daily_loss(-0.02)
        assert tripped is False
        assert state.is_kill_switch_tripped() is False

    def test_idempotent_does_not_overwrite_existing_reason(self, risk_manager, state):
        risk_manager.check_and_maybe_trip_daily_loss(-0.06)
        first_reason = state.kill_switch_reason
        risk_manager.check_and_maybe_trip_daily_loss(-0.20)
        assert state.kill_switch_reason == first_reason

    def test_target_sizing_output_cannot_bypass_kill_switch(self, risk_manager, state):
        """target_sizing 'behind pace' oldugu icin fraction_of_pool=1.0
        (maksimum) onerse bile, kill-switch tetiklendiyse RiskManager
        BUY'i yine de reddetmeli."""
        state.set_trading_enabled(True)
        risk_manager.check_and_maybe_trip_daily_loss(-0.10)
        result = risk_manager.check_order(Signal.BUY, proposed_pool_fraction=1.0)
        assert result.allowed is False
