"""Gecmis OHLCV icin yerel CSV onbellegi. Walk-forward cok sembol ve uzun
donemle calisir; her kosuda borsadan yeniden cekmek yavas ve rate-limit'e
takilir. Onbellek artimli guncellenir (sadece eksik son kisim cekilir)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from tradecrm.data.provider import (
    OHLCV_COLUMNS,
    drop_unclosed_candle,
    timeframe_to_timedelta,
)


def _cache_path(cache_dir: str, symbol: str, timeframe: str) -> Path:
    return Path(cache_dir) / f"{symbol.replace('/', '_')}_{timeframe}.csv"


def load_or_fetch(
    provider, symbol: str, timeframe: str, since: datetime, cache_dir: str = "data_cache"
) -> pd.DataFrame:
    path = _cache_path(cache_dir, symbol, timeframe)
    cached = None
    if path.exists():
        cached = pd.read_csv(path, index_col=0)
        cached.index = pd.to_datetime(cached.index, utc=True)
        cached = cached[OHLCV_COLUMNS]

    if cached is not None and len(cached) and cached.index[0] <= pd.Timestamp(since) + timeframe_to_timedelta(timeframe):
        fetch_from = cached.index[-1].to_pydatetime() + timedelta(milliseconds=1)
        fresh = provider.fetch_ohlcv_historical(symbol, timeframe=timeframe, since=fetch_from)
        df = pd.concat([cached, fresh]) if len(fresh) else cached
    else:
        df = provider.fetch_ohlcv_historical(symbol, timeframe=timeframe, since=since)

    if df.empty:
        return df
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = drop_unclosed_candle(df, timeframe, now=datetime.now(UTC))
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df[df.index >= pd.Timestamp(since)]
