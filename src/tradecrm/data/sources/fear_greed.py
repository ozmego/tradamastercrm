"""Crypto Fear & Greed Index (alternative.me), Oneri 11c.

Gunluk deger (0 = asiri korku, 100 = asiri acgozluluk), 2018'den beri.
Gozlem: D gunu etiketli deger, D 00:00 UTC'den ONCE yayinlaniyor; bu
yuzden degeri etiket zamanindan itibaren kullanmak lookahead degildir.
"""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

_URL = "https://api.alternative.me/fng/"


class FearGreedUnavailableError(Exception):
    pass


class FearGreedSource:
    name = "fng"

    def __init__(self, cache_dir: str = "data_cache", timeout: float = 10.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout

    def fetch(self, limit: int = 0) -> pd.Series:
        """limit=0: tum gecmis. index: etiket zamani (UTC gun baslangici)."""
        try:
            resp = requests.get(_URL, params={"limit": limit, "format": "json"}, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()["data"]
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise FearGreedUnavailableError(f"{type(exc).__name__}") from None
        idx = pd.to_datetime([int(d["timestamp"]) for d in data], unit="s", utc=True)
        s = pd.Series([float(d["value"]) for d in data], index=idx, name="fng")
        return s[~s.index.duplicated(keep="last")].sort_index()

    def load_or_fetch(self, max_age_hours: float = 12.0) -> pd.Series:
        path = self.cache_dir / "fear_greed.csv"
        if path.exists():
            age_h = (datetime.now(UTC).timestamp() - path.stat().st_mtime) / 3600
            if age_h < max_age_hours:
                s = pd.read_csv(path, index_col=0)["fng"]
                s.index = pd.to_datetime(s.index, utc=True)
                return s
        s = self.fetch(limit=0)
        path.parent.mkdir(parents=True, exist_ok=True)
        s.to_csv(path)
        return s

    def recent(self) -> pd.Series:
        return self.fetch(limit=10)
