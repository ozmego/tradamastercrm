from tradecrm.strategy.base import Strategy
from tradecrm.strategy.donchian_breakout import (
    DonchianBreakdownStrategy,
    DonchianBreakoutStrategy,
)
from tradecrm.strategy.mean_reversion import Rsi2MeanReversionStrategy
from tradecrm.strategy.sma_rsi_crossover import EmaRsiCrossoverStrategy

STRATEGIES: dict[str, type[Strategy]] = {
    EmaRsiCrossoverStrategy.name: EmaRsiCrossoverStrategy,
    DonchianBreakoutStrategy.name: DonchianBreakoutStrategy,
    DonchianBreakdownStrategy.name: DonchianBreakdownStrategy,
    Rsi2MeanReversionStrategy.name: Rsi2MeanReversionStrategy,
}


def register_strategy(strategy_cls: type[Strategy]) -> type[Strategy]:
    STRATEGIES[strategy_cls.name] = strategy_cls
    return strategy_cls


def get_strategy(name: str, **kwargs) -> Strategy:
    if name not in STRATEGIES:
        available = ", ".join(sorted(STRATEGIES))
        raise KeyError(f"Bilinmeyen strateji: {name!r}. Mevcut: {available}")
    return STRATEGIES[name](**kwargs)
