"""Binance USDT-M perpetual funding rate (Oneri 11a).

Funding her 8 saatte bir (00/08/16 UTC) gerceklesir; o anda bilinir.
Yuksek pozitif funding = kaldiracli long'lar kalabalik -> asagi yonlu
likidasyon kaskadi riski. Spot bot icin bir "BUY'i ertele" sinyali olarak
kullanilir. Gecmisi 2019'dan beri mevcut (backtest edilebilir).
"""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ccxt
import pandas as pd


def perp_symbol(spot_symbol: str) -> str:
    base, quote = spot_symbol.split("/")
    return f"{base}/{quote}:{quote}"


class FundingRateSource:
    name = "funding"

    def __init__(self, exchange=None, cache_dir: str = "data_cache") -> None:
        self.exchange = exchange or ccxt.binanceusdm({"enableRateLimit": True})
        self.cache_dir = Path(cache_dir)

    def fetch_history(self, symbol: str, since: datetime, until: datetime | None = None) -> pd.Series:
        """index: funding zamani (UTC), deger: funding orani (8 saatlik)."""
        cursor = int(since.timestamp() * 1000)
        until_ms = int((until or datetime.now(UTC)).timestamp() * 1000)
        rows: list[tuple[int, float]] = []
        while cursor < until_ms:
            batch = self.exchange.fetch_funding_rate_history(perp_symbol(symbol), since=cursor, limit=1000)
            if not batch:
                break
            rows.extend((int(b["timestamp"]), float(b["fundingRate"])) for b in batch)
            last = int(batch[-1]["timestamp"])
            if last <= cursor or len(batch) < 1000:
                break
            cursor = last + 1
            rate_limit = getattr(self.exchange, "rateLimit", 0) or 0
            if rate_limit:
                time.sleep(rate_limit / 1000)
        if not rows:
            return pd.Series(dtype=float, name="funding_rate")
        idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
        # Binance zaman damgalari birkac ms kayabiliyor (08:00:00.008) -> saniyeye yuvarla.
        idx = idx.floor("s")
        s = pd.Series([r[1] for r in rows], index=idx, name="funding_rate")
        return s[~s.index.duplicated(keep="last")].sort_index()

    def load_or_fetch(self, symbol: str, since: datetime) -> pd.Series:
        path = self.cache_dir / f"funding_{symbol.replace('/', '_')}.csv"
        cached = None
        if path.exists():
            cached = pd.read_csv(path, index_col=0)["funding_rate"]
            cached.index = pd.to_datetime(cached.index, utc=True)
        if cached is not None and len(cached) and cached.index[0] <= pd.Timestamp(since) + timedelta(hours=8):
            fresh = self.fetch_history(symbol, cached.index[-1].to_pydatetime() + timedelta(seconds=1))
            s = pd.concat([cached, fresh]) if len(fresh) else cached
        else:
            s = self.fetch_history(symbol, since)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        path.parent.mkdir(parents=True, exist_ok=True)
        s.rename("funding_rate").to_csv(path)
        return s

    def recent(self, symbol: str, days: int = 3) -> pd.Series:
        return self.fetch_history(symbol, datetime.now(UTC) - timedelta(days=days))
