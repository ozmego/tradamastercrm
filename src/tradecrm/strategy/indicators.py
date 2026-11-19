"""Gosterge hesaplari icin tek nokta. pandas_ta bakimi zayif bir paket;
ileride degistirmek gerekirse sadece bu dosya degisir.

Butun fonksiyonlar nedensel (causal): i. degerin hesabinda sadece
0..i barlari kullanilir (lookahead yok)."""

import pandas as pd
import pandas_ta as ta


def _empty(df_or_series) -> pd.Series:
    return pd.Series(float("nan"), index=df_or_series.index, dtype=float)


def ema(close: pd.Series, length: int) -> pd.Series:
    out = ta.ema(close, length=length)
    return out if out is not None else _empty(close)


def rsi(close: pd.Series, length: int) -> pd.Series:
    out = ta.rsi(close, length=length)
    return out if out is not None else _empty(close)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    out = ta.atr(df["High"], df["Low"], df["Close"], length=length)
    return out if out is not None else _empty(df)


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    out = ta.adx(df["High"], df["Low"], df["Close"], length=length)
    if out is None:
        return _empty(df)
    return out[f"ADX_{length}"]
