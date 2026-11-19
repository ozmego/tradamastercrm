from typing import ClassVar

import numpy as np
import pandas as pd

from tradecrm.strategy.base import VectorizedStrategy


class DonchianBreakoutStrategy(VectorizedStrategy):
    """Kanal kirilimi (trend takibi, Oneri 7).

    - Kapanis, onceki `entry_period` barin en yuksegini asarsa BUY.
    - Kapanis, onceki `exit_period` barin en dusugunun altina inerse SELL.
    Asil risk kontrolu cikis katmanindadir (ATR stop + trailing).
    """

    name = "donchian_breakout"
    param_grid: ClassVar[dict[str, list]] = {"entry_period": [20, 40, 55], "exit_period": [10, 20]}

    def __init__(self, entry_period: int = 40, exit_period: int = 20) -> None:
        if exit_period <= 0 or entry_period <= 0:
            raise ValueError("periyotlar pozitif olmali")
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.required_lookback = max(entry_period, exit_period) + 1

    def params(self) -> dict:
        return {"entry_period": self.entry_period, "exit_period": self.exit_period}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        upper = df["High"].rolling(self.entry_period).max().shift(1)
        lower = df["Low"].rolling(self.exit_period).min().shift(1)
        close = df["Close"]
        codes = np.zeros(len(df), dtype=np.int8)
        codes[(close > upper).to_numpy()] = 1
        codes[(close < lower).to_numpy()] = -1
        return pd.Series(codes, index=df.index, name="signal")
