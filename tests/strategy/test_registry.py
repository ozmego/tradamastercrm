import pytest

from tradecrm.strategy.base import Signal, Strategy
from tradecrm.strategy.registry import STRATEGIES, get_strategy, register_strategy
from tradecrm.strategy.sma_rsi_crossover import EmaRsiCrossoverStrategy


def test_ema_rsi_crossover_registered_by_default():
    assert "ema_rsi_crossover" in STRATEGIES
    assert STRATEGIES["ema_rsi_crossover"] is EmaRsiCrossoverStrategy


def test_get_strategy_returns_instance():
    strategy = get_strategy("ema_rsi_crossover", fast_period=3, slow_period=7)
    assert isinstance(strategy, EmaRsiCrossoverStrategy)
    assert strategy.fast_period == 3


def test_get_strategy_unknown_name_raises():
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")


def test_register_strategy_adds_to_registry():
    class DummyStrategy(Strategy):
        name = "dummy_for_test"

        def generate_signal(self, df):
            return Signal.HOLD

    register_strategy(DummyStrategy)
    try:
        assert get_strategy("dummy_for_test").name == "dummy_for_test"
    finally:
        del STRATEGIES["dummy_for_test"]
