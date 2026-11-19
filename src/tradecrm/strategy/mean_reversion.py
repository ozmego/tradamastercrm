from typing import ClassVar

import numpy as np
import pandas as pd

from tradecrm.strategy import indicators as ind
from tradecrm.strategy.base import VectorizedStrategy


class Rsi2MeanReversionStrategy(VectorizedStrategy):
    """Kisa vadeli ortalamaya donus (Oneri 8), Connors RSI(2) tarzi.

    - RSI(rsi_period) < entry_rsi (kisa vadede asiri satim) -> BUY
    - Kapanis SMA(exit_sma) ustune cikinca -> SELL
    Uzun vadeli trend kosulu (fiyat > EMA200) profil filtresiyle eklenir;
    sadece yukari trendde "dip alimi" yapilir (spot, long-only).
    Bilerek az parametreli: genis parametre uzayi = overfitting.
    """

    name = "rsi2_mean_reversion"
    param_grid: ClassVar[dict[str, list]] = {"entry_rsi": [5, 10, 15], "exit_sma": [5, 10]}

    def __init__(self, rsi_period: int = 2, entry_rsi: float = 10.0, exit_sma: int = 5) -> None:
        if rsi_period < 2 or exit_sma < 2:
            raise ValueError("rsi_period ve exit_sma >= 2 olmali")
        self.rsi_period = rsi_period
        self.entry_rsi = entry_rsi
        self.exit_sma = exit_sma
        self.required_lookback = max(rsi_period * 10, exit_sma) + 1

    def params(self) -> dict:
        return {"rsi_period": self.rsi_period, "entry_rsi": self.entry_rsi, "exit_sma": self.exit_sma}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["Close"]
        rsi = ind.rsi(close, self.rsi_period)
        sma = close.rolling(self.exit_sma).mean()
        codes = np.zeros(len(df), dtype=np.int8)
        codes[(rsi < self.entry_rsi).to_numpy()] = 1
        codes[(close > sma).to_numpy()] = -1  # cikis onceliklidir (ayni barda ikisi olamaz: rsi dusukken close < sma)
        codes[: self.required_lookback - 1] = 0
        return pd.Series(codes, index=df.index, name="signal")
