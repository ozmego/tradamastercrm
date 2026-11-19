import pytest

from tradecrm.risk.guardrails import RiskManager
from tradecrm.risk.state import RiskStateStore
from tradecrm.strategy.base import Signal


@pytest.fixture()
def state(db_session) -> RiskStateStore:
    return RiskStateStore(db_session)


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


class TestCheckBuyExposureAndEnv:
    """Tutar bazli, butun acik pozisyonlari ve .env salterini goren kontrol."""

    def _snap(self, cash, market_value):
        from tradecrm.broker.base import AccountSnapshot

        return AccountSnapshot(cash=cash, allocated=market_value, market_value=market_value)

    def test_caps_by_total_exposure(self, risk_manager, state):
        state.set_trading_enabled(True)
        res = risk_manager.check_buy(self._snap(cash=5_000, market_value=5_000), desired_notional=5_000)
        # equity 10k, tavan 7.5k, acik 5k -> en fazla 2.5k
        assert res.allowed
        assert res.approved_notional == pytest.approx(2_500)
        assert res.reason == "ok_capped"

    def test_cap_is_relative_to_total_equity(self, risk_manager, state):
        state.set_trading_enabled(True)
        res = risk_manager.check_buy(self._snap(cash=1_000, market_value=0), desired_notional=5_000)
        assert res.approved_notional == pytest.approx(750)  # %75 * 1.000

    def test_cash_bound_when_snapshot_reports_less_cash(self, state):
        """Nakit siniri ikinci savunma: tavan %100 olsa bile nakitten fazla emir yok."""
        state.set_trading_enabled(True)
        rm = RiskManager(state, total_capital_allocation_pct=1.0)
        res = rm.check_buy(self._snap(cash=1_000, market_value=500), desired_notional=5_000)
        assert res.approved_notional == pytest.approx(1_000)

    def test_blocked_when_cap_reached(self, risk_manager, state):
        state.set_trading_enabled(True)
        res = risk_manager.check_buy(self._snap(cash=2_500, market_value=7_500), desired_notional=1_000)
        assert not res.allowed
        assert res.reason.startswith("exposure_cap_reached")

    def test_below_min_notional(self, risk_manager, state):
        state.set_trading_enabled(True)
        res = risk_manager.check_buy(self._snap(cash=10_000, market_value=0), desired_notional=5)
        assert not res.allowed
        assert res.reason.startswith("below_min_notional")

    def test_live_account_requires_env_master_switch(self, db_session):
        live_state = RiskStateStore(db_session, broker="binance")
        live_state.set_trading_enabled(True)
        live_state.confirm_first_order()
        snap = self._snap(cash=10_000, market_value=0)

        blocked = RiskManager(live_state, live_trading_allowed_by_env=False).check_buy(snap, 1_000, True)
        assert not blocked.allowed
        assert blocked.reason.startswith("env_live_trading_disabled")

        allowed = RiskManager(live_state, live_trading_allowed_by_env=True).check_buy(snap, 1_000, True)
        assert allowed.allowed

    def test_env_switch_irrelevant_for_paper(self, risk_manager, state):
        state.set_trading_enabled(True)
        assert risk_manager.check_buy(self._snap(cash=10_000, market_value=0), 1_000).allowed
