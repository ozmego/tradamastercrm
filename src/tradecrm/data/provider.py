import time
from datetime import datetime, timezone

import ccxt
import pandas as pd

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class CCXTDataProvider:
    """Gecmis + canli OHLCV verisi icin tek arayuz (ccxt uzerinden).

    backtesting.py'nin bekledigi sekilde Open/High/Low/Close/Volume
    kolonlari + UTC DatetimeIndex dondurur.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        api_secret: str = "",
        exchange: ccxt.Exchange | None = None,
    ) -> None:
        if exchange is not None:
            self.exchange = exchange
        else:
            exchange_class = getattr(ccxt, exchange_id)
            config: dict = {"enableRateLimit": True}
            if api_key and api_secret:
                config["apiKey"] = api_key
                config["secret"] = api_secret
            self.exchange = exchange_class(config)

    def fetch_ohlcv_historical(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        until: datetime | None = None,
        limit_per_call: int = 1000,
    ) -> pd.DataFrame:
        """Sayfalanmis gecmis OHLCV verisi ceker (since'den until'a kadar, veya borsanin verdigi en eski veriden bugune)."""
        since_ms = _to_ms(since)
        until_ms = _to_ms(until)

        all_candles: list[list] = []
        cursor = since_ms
        while True:
            batch = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit_per_call)
            if not batch:
                break
            all_candles.extend(batch)
            last_ts = batch[-1][0]
            if until_ms is not None and last_ts >= until_ms:
                break
            if len(batch) < limit_per_call:
                break
            cursor = last_ts + 1
            rate_limit = getattr(self.exchange, "rateLimit", 0) or 0
            if rate_limit:
                time.sleep(rate_limit / 1000)

        return _to_dataframe(all_candles, until_ms)

    def fetch_latest_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> pd.DataFrame:
        """Son N mumu ceker (canli kullanim icin)."""
        batch = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return _to_dataframe(batch, None)

    def fetch_latest_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])


def _to_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _to_dataframe(candles: list[list], until_ms: int | None) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    df = pd.DataFrame(candles, columns=["timestamp", *OHLCV_COLUMNS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates(subset="timestamp").set_index("timestamp").sort_index()
    if until_ms is not None:
        df = df[df.index <= pd.to_datetime(until_ms, unit="ms", utc=True)]
    return df
