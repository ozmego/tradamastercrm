from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy(ABC):
    """Butun stratejilerin uydugu arayuz. Backtest ve canli calisma ayni
    generate_signal metodunu cagirir, boylece iki ortam arasinda mantik
    sapmasi olmaz."""

    name: str = "base"
    required_lookback: int = 0

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """df: Open/High/Low/Close/Volume kolonlu, kronolojik sirali DataFrame.
        Sadece df.iloc[-1]'e kadar olan veriyi kullanmali (lookahead yok)."""
        raise NotImplementedError
