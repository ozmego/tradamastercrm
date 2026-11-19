"""Ek veri kaynaklarini OHLCV DataFrame'ine kolon olarak ekler.

Ayni fonksiyon backtest (tum gecmis) ve canli (son veriler) icin
kullanilir; fark sadece kaynak serilerin nereden geldigidir.

Kolonlar:
- funding_rate      : son bilinen 8h funding orani
- fng               : son bilinen Fear & Greed degeri
- hours_to_event    : sonraki makro olaya kalan saat
- hours_since_event : son makro olaydan gecen saat
- btc_close         : barin kapanisinda bilinen son BTC/USDT kapanisi (Oneri 11e)
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd

from tradecrm.data.sources.align import align_asof
from tradecrm.data.sources.macro import event_distance_columns, load_events

logger = logging.getLogger(__name__)

SOURCE_COLUMNS = {
    "funding": ["funding_rate"],
    "fng": ["fng"],
    "macro": ["hours_to_event", "hours_since_event"],
    "btc": ["btc_close"],
}

BTC_SYMBOL = "BTC/USDT"


def btc_known_at(btc_ohlcv: pd.DataFrame, timeframe: str) -> pd.Series:
    """BTC kapanislari, bilindikleri an (mumun KAPANIS zamani) ile indekslenir."""
    from tradecrm.data.provider import timeframe_to_timedelta

    return pd.Series(btc_ohlcv["Close"].to_numpy(dtype=float),
                     index=btc_ohlcv.index + timeframe_to_timedelta(timeframe))


@dataclass
class SourceData:
    funding: dict[str, pd.Series] = field(default_factory=dict)  # sembol -> seri
    fng: pd.Series | None = None
    macro_events: pd.DatetimeIndex | None = None
    btc: pd.Series | None = None  # btc_known_at() ciktisi


def enrich(df: pd.DataFrame, symbol: str, timeframe: str, sources: set[str], data: SourceData) -> pd.DataFrame:
    if not sources:
        return df
    out = df.copy()
    if "funding" in sources:
        out["funding_rate"] = align_asof(data.funding.get(symbol), df, timeframe)
    if "fng" in sources:
        out["fng"] = align_asof(data.fng, df, timeframe)
    if "macro" in sources:
        events = data.macro_events if data.macro_events is not None else pd.DatetimeIndex([], tz="UTC")
        out = out.join(event_distance_columns(df, timeframe, events))
    if "btc" in sources:
        out["btc_close"] = align_asof(data.btc, df, timeframe)
    return out


def load_historical(symbols: list[str], sources: set[str], since: datetime, timeframe: str | None = None,
                    btc_ohlcv: pd.DataFrame | None = None) -> SourceData:
    """Backtest icin: tum gecmis (onbellekli). btc_ohlcv verilmezse (ve "btc"
    isteniyorsa) ayni zaman diliminde BTC/USDT yuklenir."""
    data = SourceData()
    if "btc" in sources:
        if timeframe is None:
            raise ValueError("btc kaynagi icin timeframe gerekli")
        if btc_ohlcv is None:
            from tradecrm.data.cache import load_or_fetch
            from tradecrm.data.provider import CCXTDataProvider

            btc_ohlcv = load_or_fetch(CCXTDataProvider(), BTC_SYMBOL, timeframe, since)
        data.btc = btc_known_at(btc_ohlcv, timeframe)
    if "funding" in sources:
        from tradecrm.data.sources.funding import FundingRateSource

        src = FundingRateSource()
        for sym in symbols:
            data.funding[sym] = src.load_or_fetch(sym, since - timedelta(days=2))
    if "fng" in sources:
        from tradecrm.data.sources.fear_greed import FearGreedSource

        data.fng = FearGreedSource().load_or_fetch()
    if "macro" in sources:
        data.macro_events = load_events()
    return data


class LiveEnricher:
    """Canli runner icin: her tick'te kaynaklarin son degerlerini ceker.
    Bir kaynak erisilemezse kolonu NaN birakir (filtreler NaN'i "engelleme"
    sayar: bilinmeyen veri tek basina durdurma sebebi degildir) ve audit'e
    yazar."""

    def __init__(self, symbol: str, timeframe: str, sources: set[str], audit_logger=None, provider=None) -> None:
        self.symbol = symbol
        self.provider = provider  # btc kaynagi icin (fetch_latest_ohlcv)
        self.timeframe = timeframe
        self.sources = sources
        self.audit_logger = audit_logger
        self._funding_src = None
        self._fng_src = None
        self._events = load_events() if "macro" in sources else None

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        data = SourceData(macro_events=self._events)
        if "funding" in self.sources:
            try:
                from tradecrm.data.sources.funding import FundingRateSource

                self._funding_src = self._funding_src or FundingRateSource()
                data.funding[self.symbol] = self._funding_src.recent(self.symbol)
            except Exception as exc:  # noqa: BLE001 -- kaynak hatasi tick'i durdurmamali
                self._unavailable("funding", exc)
        if "fng" in self.sources:
            try:
                from tradecrm.data.sources.fear_greed import FearGreedSource

                self._fng_src = self._fng_src or FearGreedSource()
                data.fng = self._fng_src.recent()
            except Exception as exc:  # noqa: BLE001
                self._unavailable("fng", exc)
        if "btc" in self.sources:
            try:
                if self.symbol == BTC_SYMBOL:
                    btc = df
                else:
                    if self.provider is None:
                        from tradecrm.data.provider import CCXTDataProvider

                        self.provider = CCXTDataProvider()
                    btc = self.provider.fetch_latest_ohlcv(BTC_SYMBOL, timeframe=self.timeframe,
                                                           limit=max(len(df), 300))
                # Henuz kapanmamis BTC mumu align_asof'ta zaten disarida kalir.
                data.btc = btc_known_at(btc, self.timeframe)
            except Exception as exc:  # noqa: BLE001
                self._unavailable("btc", exc)
        return enrich(df, self.symbol, self.timeframe, self.sources, data)

    def _unavailable(self, name: str, exc: Exception) -> None:
        logger.warning("Veri kaynagi %s erisilemedi: %s", name, type(exc).__name__)
        if self.audit_logger is not None:
            self.audit_logger.log(
                "source_unavailable", source=name, symbol=self.symbol, error=type(exc).__name__,
                at=datetime.now(UTC).isoformat(),
            )
