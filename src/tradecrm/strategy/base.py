from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

import numpy as np
import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# Vektorel sinyal serilerinde kullanilan sayisal kodlama.
SIGNAL_CODES = {Signal.BUY: 1, Signal.SELL: -1, Signal.HOLD: 0}
CODE_TO_SIGNAL = {v: k for k, v in SIGNAL_CODES.items()}


class Strategy(ABC):
    """Butun stratejilerin uydugu arayuz. Backtest ve canli calisma ayni
    sinyal mantigini cagirir, boylece iki ortam arasinda mantik sapmasi olmaz."""

    name: str = "base"
    required_lookback: int = 0
    # Walk-forward optimizasyonu icin kucuk bir parametre izgarasi
    # (bilerek kucuk: genis izgara = overfitting).
    param_grid: ClassVar[dict[str, list]] = {}

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """df: Open/High/Low/Close/Volume kolonlu, kronolojik sirali DataFrame.
        Sadece df.iloc[-1]'e kadar olan veriyi kullanmali (lookahead yok)."""
        raise NotImplementedError

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Her bar icin sinyal kodu (1/-1/0). Varsayilan: bar bar
        generate_signal cagirir (yavas ama dogru). Vektorel stratejiler
        bunu override eder."""
        codes = np.zeros(len(df), dtype=np.int8)
        for i in range(len(df)):
            codes[i] = SIGNAL_CODES[self.generate_signal(df.iloc[: i + 1])]
        return pd.Series(codes, index=df.index, name="signal")

    def params(self) -> dict:
        return {}


class VectorizedStrategy(Strategy):
    """Mantigi tek bir yerde -- generate_signals'ta -- tanimlanan strateji.
    generate_signal (canli) son barin kodunu dondurur; backtest tum seriyi
    kullanir. Ikisi ayni koddan uretildigi icin birebir tutarlidir."""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) == 0:
            return Signal.HOLD
        return CODE_TO_SIGNAL[int(self.generate_signals(df).iloc[-1])]
