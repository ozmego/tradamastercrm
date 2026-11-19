from typing import ClassVar

import numpy as np
import pandas as pd

from tradecrm.strategy import indicators as ind
from tradecrm.strategy.base import Signal, VectorizedStrategy


class EmaRsiCrossoverStrategy(VectorizedStrategy):
    """Baslangic stratejisi: hizli/yavas EMA kesisimi + RSI filtresi.

    - EMA(fast) yukari yonlu EMA(slow)'u kesince VE RSI asiri-alim
      esiginin altindaysa BUY.
    - EMA(fast) asagi yonlu EMA(slow)'u kesince SELL (RSI'dan bagimsiz --
      bu bir cikis/stop sinyali, filtrelenmemeli).
    - Diger her durumda HOLD.
    """

    name = "ema_rsi_crossover"
    param_grid: ClassVar[dict[str, list]] = {"fast_period": [9, 12, 15], "slow_period": [21, 26, 34]}

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        rsi_period: int = 14,
        rsi_overbought: float = 70.0,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period slow_period'dan kucuk olmali")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.required_lookback = slow_period + rsi_period + 1

    def params(self) -> dict:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "rsi_period": self.rsi_period,
            "rsi_overbought": self.rsi_overbought,
        }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n = len(df)
        codes = np.zeros(n, dtype=np.int8)
        if n < self.required_lookback:
            return pd.Series(codes, index=df.index, name="signal")

        close = df["Close"]
        diff = ind.ema(close, self.fast_period) - ind.ema(close, self.slow_period)
        prev_diff = diff.shift(1)
        rsi = ind.rsi(close, self.rsi_period)

        # NaN karsilastirmalari False dondurur -> HOLD (bar-bar mantikla ayni).
        crossed_up = (prev_diff <= 0) & (diff > 0)
        crossed_down = (prev_diff >= 0) & (diff < 0)
        buy = crossed_up & (rsi < self.rsi_overbought)

        codes[buy.to_numpy()] = 1
        codes[crossed_down.to_numpy()] = -1
        # Eski bar-bar davranis: len(df) < required_lookback iken HOLD.
        codes[: self.required_lookback - 1] = 0
        return pd.Series(codes, index=df.index, name="signal")


def decide_signal(prev_diff: float, curr_diff: float, rsi_value: float, rsi_overbought: float) -> Signal:
    """Saf karar mantigi: EMA fark(fast-slow) kesisimi + RSI filtresi.
    generate_signals'taki vektorel mantigin skaler karsiligi (testler icin)."""
    crossed_up = prev_diff <= 0 and curr_diff > 0
    crossed_down = prev_diff >= 0 and curr_diff < 0

    if crossed_up and rsi_value < rsi_overbought:
        return Signal.BUY
    if crossed_down:
        return Signal.SELL
    return Signal.HOLD
