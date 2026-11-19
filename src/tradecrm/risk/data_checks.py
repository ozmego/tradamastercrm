"""Yeni alim oncesi piyasa verisi kontrolleri. Herhangi bir sorun varsa
yeni alim yapilmaz; satis/cikis yolu acik kalir.

- stale_signal : son kapanmis mum, simdiye gore yarim mumdan daha eski
                 (simulator girisi mum kapanisindan hemen sonra varsayar;
                 bayat sinyalle girmek bu varsayimi bozar)
- invalid_ohlc : NaN, <=0 fiyat, High < max(Open, Close), Low > min(Open, Close)
- data_gap     : son `gap_window` barda eksik mum (gostergeler bozulur)
- invalid_price/price_deviation: anlik fiyat gecersiz ya da son kapanistan
                 asiri sapmis (hatali veri / flash crash)
"""

from datetime import datetime

import numpy as np
import pandas as pd

from tradecrm.data.provider import timeframe_to_timedelta


def validate_market_data(
    df: pd.DataFrame,
    timeframe: str,
    now: datetime,
    ticker_price: float | None = None,
    gap_window: int = 300,
    max_price_deviation: float = 0.10,
    staleness_fraction: float = 0.5,
) -> list[str]:
    problems: list[str] = []
    if df is None or len(df) == 0:
        return ["no_data"]
    tf = timeframe_to_timedelta(timeframe)
    last_close_time = df.index[-1] + tf
    if pd.Timestamp(now) - last_close_time > tf * staleness_fraction:
        problems.append(f"stale_signal (son kapanis {last_close_time})")

    tail = df.iloc[-gap_window:]
    bad = invalid_ohlc_mask(tail)
    if bad.any():
        problems.append(f"invalid_ohlc ({int(bad.sum())} bar)")

    if len(tail) > 1:
        steps = pd.Series(tail.index).diff().dropna()
        gaps = int((steps != tf).sum())
        if gaps:
            problems.append(f"data_gap ({gaps} bosluk)")

    if ticker_price is not None:
        last_close = float(df["Close"].iloc[-1])
        if not np.isfinite(ticker_price) or ticker_price <= 0:
            problems.append(f"invalid_price ({ticker_price})")
        elif last_close > 0 and abs(ticker_price / last_close - 1) > max_price_deviation:
            problems.append(f"price_deviation ({ticker_price / last_close - 1:+.1%})")
    return problems


def invalid_ohlc_mask(df: pd.DataFrame) -> np.ndarray:
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    with np.errstate(invalid="ignore"):
        return (
            ~np.isfinite(o) | ~np.isfinite(h) | ~np.isfinite(lo) | ~np.isfinite(c)
            | (o <= 0) | (h <= 0) | (lo <= 0) | (c <= 0)
            | (h < np.maximum(o, c) - 1e-12) | (lo > np.minimum(o, c) + 1e-12)
        )


def last_bar_is_valid(df: pd.DataFrame) -> bool:
    """Cikis seviyesi kontrolu icin: hatali bir bar (orn. Low=0) yanlis stop
    tetiklememeli."""
    return df is not None and len(df) > 0 and not invalid_ohlc_mask(df.iloc[-1:]).any()
