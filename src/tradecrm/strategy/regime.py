"""Basit, kural tabanli piyasa rejimi siniflandirici (Oneri 4/8).

- "high_vol": ATR/fiyat orani son `vol_window` barin ust yuzdeliginde
- "trend"   : ADX >= trend_adx
- "range"   : ADX <= range_adx
- "neutral" : arada
Nedenseldir (sadece gecmis barlar).
"""

import numpy as np
import pandas as pd

from tradecrm.strategy import indicators as ind

REGIMES = ("trend", "range", "neutral", "high_vol")


def classify_regime(
    df: pd.DataFrame,
    adx_length: int = 14,
    trend_adx: float = 25.0,
    range_adx: float = 20.0,
    atr_length: int = 14,
    vol_window: int = 500,
    high_vol_percentile: float = 0.9,
) -> pd.Series:
    adx = ind.adx(df, adx_length)
    natr = ind.atr(df, atr_length) / df["Close"]
    vol_rank = natr.rolling(vol_window, min_periods=vol_window // 2).rank(pct=True)

    out = np.full(len(df), "neutral", dtype=object)
    out[(adx <= range_adx).to_numpy()] = "range"
    out[(adx >= trend_adx).to_numpy()] = "trend"
    out[(vol_rank >= high_vol_percentile).to_numpy()] = "high_vol"
    out[adx.isna().to_numpy()] = "neutral"
    return pd.Series(out, index=df.index, name="regime")
