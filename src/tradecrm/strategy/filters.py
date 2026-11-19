"""Birlestirilebilir giris filtreleri (Oneri 4, 5, 6).

Teyit filtresi (confirm) sinyali bir bar geciktirir; digerleri maskedir.

Bir filtre her bar icin "BUY'a izin var mi" maskesi uretir. Filtreler
SADECE BUY'i bastirir; SELL (cikis) asla filtrelenmez -- ayni felsefe
news_aware.py ve guardrails.py'de de gecerli.

Butun maskeler nedenseldir: i. bar icin sadece 0..i barlari kullanilir.
Ust zaman dilimi filtresi yalnizca KAPANMIS ust zaman dilimi mumlarini
kullanir.
"""

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from tradecrm.data.provider import timeframe_to_timedelta
from tradecrm.strategy import indicators as ind
from tradecrm.strategy.base import Strategy, VectorizedStrategy


class EntryFilter(ABC):
    name: str = "filter"
    required_lookback: int = 0
    # Ek veri kaynagi gerektiren filtreler (bkz. data/sources/enrich.py).
    source: str | None = None

    @abstractmethod
    def mask(self, df: pd.DataFrame) -> pd.Series:
        """True: bu barda BUY'a izin var."""
        raise NotImplementedError

    def transform(self, df: pd.DataFrame, codes: np.ndarray) -> np.ndarray:
        """Sinyalleri maskeden once donusturen filtreler icin (orn. teyit).
        Varsayilan: degisiklik yok."""
        return codes

    def allows_trade(self, df: pd.DataFrame, signal_pos: np.ndarray) -> np.ndarray:
        """Katki analizi (backtest/filter_test.py): taban stratejinin `signal_pos`
        barlarindaki BUY'larindan hangileri bu filtreden gecerdi."""
        m = self.mask(df).fillna(False).to_numpy(dtype=bool)
        return m[signal_pos]


class TrendFilter(EntryFilter):
    """Fiyat uzun EMA'nin ustunde VE uzun EMA yukari egimli."""

    name = "trend"

    def __init__(self, ema_length: int = 200, slope_bars: int | None = 24) -> None:
        self.ema_length = ema_length
        self.slope_bars = slope_bars  # None: egim sarti yok, sadece fiyat > EMA
        self.required_lookback = ema_length + (slope_bars or 0)

    def mask(self, df):
        e = ind.ema(df["Close"], self.ema_length)
        above = df["Close"] > e
        if self.slope_bars:
            above &= e > e.shift(self.slope_bars)
        return above


class AdxFilter(EntryFilter):
    """Trend gucu: ADX esigin ustunde (yatay piyasada whipsaw'i eler)."""

    name = "adx"

    def __init__(self, length: int = 14, min_adx: float = 20.0) -> None:
        self.length = length
        self.min_adx = min_adx
        self.required_lookback = 3 * length

    def mask(self, df):
        return ind.adx(df, self.length) > self.min_adx


class HigherTimeframeTrendFilter(EntryFilter):
    """Ust zaman diliminde (orn. 4h) kapanis > EMA(n). Taban bar t'nin
    kapanis aninda, o ana kadar KAPANMIS en son ust-TF mumu kullanilir."""

    name = "htf_trend"

    def __init__(self, base_timeframe: str = "1h", higher_timeframe: str = "4h", ema_length: int = 50) -> None:
        self.base_td = timeframe_to_timedelta(base_timeframe)
        self.htf_td = timeframe_to_timedelta(higher_timeframe)
        self.ema_length = ema_length
        self.required_lookback = int(ema_length * (self.htf_td / self.base_td)) + 1

    def mask(self, df):
        htf_close = df["Close"].resample(self.htf_td, label="left", closed="left", origin="epoch").last().dropna()
        cond = htf_close > ind.ema(htf_close, self.ema_length)
        cond.index = cond.index + self.htf_td  # deger ust-TF mumunun KAPANISINDA bilinir
        base_close_times = df.index + self.base_td
        aligned = cond.reindex(cond.index.union(base_close_times)).ffill().reindex(base_close_times)
        return pd.Series(aligned.fillna(False).astype(bool).to_numpy(), index=df.index)


class VolumeFilter(EntryFilter):
    name = "volume"

    def __init__(self, length: int = 20, multiplier: float = 1.2) -> None:
        self.length = length
        self.multiplier = multiplier
        self.required_lookback = length + 1

    def mask(self, df):
        avg = df["Volume"].rolling(self.length).mean().shift(1)
        return df["Volume"] > self.multiplier * avg


class VolatilityCapFilter(EntryFilter):
    """ATR/fiyat orani, son N bardaki dagiliminin ust yuzdeliginde ise girme
    (haber/likidasyon kaynakli asiri hareketler)."""

    name = "vol_cap"

    def __init__(self, atr_length: int = 14, window: int = 500, max_percentile: float = 0.9) -> None:
        self.atr_length = atr_length
        self.window = window
        self.max_percentile = max_percentile
        self.required_lookback = window

    def mask(self, df):
        natr = ind.atr(df, self.atr_length) / df["Close"]
        rank = natr.rolling(self.window, min_periods=self.window // 2).rank(pct=True)
        return rank < self.max_percentile


class ConfirmationFilter(EntryFilter):
    """Oneri 6: sinyal mumundan sonra bir mum daha teyit beklenir. BUY,
    ic strateji bir onceki barda BUY verdiyse VE bu barin kapanisi sinyal
    mumunun kapanisinin ustundeyse uretilir (kirilim geri verilmediyse).
    Giris boylece bir bar gecikir; sahte kirilimlarin bir kismi elenir."""

    name = "confirm"
    required_lookback = 1

    def mask(self, df):
        return pd.Series(True, index=df.index)

    def transform(self, df, codes):
        close = df["Close"].to_numpy(dtype=float)
        prev_buy = np.zeros(len(codes), dtype=bool)
        prev_buy[1:] = codes[:-1] == 1
        follow = np.zeros(len(codes), dtype=bool)
        follow[1:] = close[1:] > close[:-1]
        out = codes.copy()
        out[out == 1] = 0
        out[prev_buy & follow & (codes != -1)] = 1  # ayni barda SELL varsa SELL kazanir
        return out

    def allows_trade(self, df, signal_pos):
        close = df["Close"].to_numpy(dtype=float)
        nxt = np.minimum(signal_pos + 1, len(close) - 1)
        return (signal_pos + 1 < len(close)) & (close[nxt] > close[signal_pos])


class RegimeFilter(EntryFilter):
    """Sadece izin verilen rejimlerde BUY (bkz. strategy/regime.py)."""

    name = "regime"

    def __init__(self, allowed: tuple[str, ...] = ("range", "neutral"), **regime_params) -> None:
        from tradecrm.strategy.regime import REGIMES

        unknown = set(allowed) - set(REGIMES)
        if unknown:
            raise ValueError(f"Bilinmeyen rejim(ler): {unknown}")
        self.allowed = tuple(allowed)
        self.regime_params = regime_params
        self.required_lookback = 250

    def mask(self, df):
        from tradecrm.strategy.regime import classify_regime

        return classify_regime(df, **self.regime_params).isin(self.allowed)


class _SourceFilter(EntryFilter):
    """Ek veri kolonuna bakan filtre. Kolon yoksa hata (backtest'te
    zenginlestirme unutulmus demektir); deger NaN ise girise IZIN verilir
    -- canlida kaynak gecici erisilemezse "bilmiyoruz" tek basina
    durdurma sebebi sayilmaz (news_aware.py ile ayni ilke)."""

    columns: tuple[str, ...] = ()

    def _require(self, df):
        missing = [c for c in self.columns if c not in df.columns]
        if missing:
            raise KeyError(f"{self.name} filtresi icin eksik kolon(lar): {missing} -- veriyi enrich() ile zenginlestirin")


class FundingFilter(_SourceFilter):
    """Oneri 11a: perpetual funding asiri pozitifse (kaldiracli long
    kalabaligi) BUY'i ertele."""

    name = "funding"
    source = "funding"
    columns = ("funding_rate",)

    def __init__(self, max_rate: float = 0.0003) -> None:
        self.max_rate = max_rate

    def mask(self, df):
        self._require(df)
        f = df["funding_rate"]
        return f.isna() | (f <= self.max_rate)


class FearGreedFilter(_SourceFilter):
    """Oneri 11c: asiri acgozlulukte (zirve riski) BUY'i ertele."""

    name = "fng"
    source = "fng"
    columns = ("fng",)

    def __init__(self, max_value: float = 80.0, min_value: float | None = None) -> None:
        self.max_value = max_value
        self.min_value = min_value

    def mask(self, df):
        self._require(df)
        v = df["fng"]
        ok = v <= self.max_value
        if self.min_value is not None:
            ok &= v >= self.min_value
        return v.isna() | ok


class MacroBlackoutFilter(_SourceFilter):
    """Oneri 11d: planli makro olaydan (FOMC vb.) once/sonra yeni giris yok."""

    name = "macro"
    source = "macro"
    columns = ("hours_to_event", "hours_since_event")

    def __init__(self, hours_before: float = 4.0, hours_after: float = 2.0) -> None:
        self.hours_before = hours_before
        self.hours_after = hours_after

    def mask(self, df):
        self._require(df)
        # Giris bir sonraki barin acilisinda (= bu barin kapanisinda) yapilir.
        return ~((df["hours_to_event"] <= self.hours_before) | (df["hours_since_event"] < self.hours_after))


class BtcTrendFilter(_SourceFilter):
    """Oneri 11e: BTC dusus trendindeyken (kapanis < EMA) altcoin BUY yok.
    Kripto piyasasinin buyuk kismi BTC ile birlikte hareket eder; BTC zayifken
    alt kirilimlarinin daha cok basarisiz oldugu varsayimi test edilir."""

    name = "btc_trend"
    source = "btc"
    columns = ("btc_close",)

    def __init__(self, ema_length: int = 50) -> None:
        self.ema_length = ema_length
        self.required_lookback = ema_length * 2

    def mask(self, df):
        self._require(df)
        c = df["btc_close"]
        e = c.ewm(span=self.ema_length, adjust=False, min_periods=self.ema_length).mean()
        return c.isna() | e.isna() | (c > e)


FILTERS: dict[str, type[EntryFilter]] = {
    cls.name: cls
    for cls in (
        TrendFilter, AdxFilter, HigherTimeframeTrendFilter, VolumeFilter, VolatilityCapFilter,
        FundingFilter, FearGreedFilter, MacroBlackoutFilter, RegimeFilter, ConfirmationFilter, BtcTrendFilter,
    )
}


def build_filter(name: str, **params) -> EntryFilter:
    if name not in FILTERS:
        raise KeyError(f"Bilinmeyen filtre: {name!r}. Mevcut: {', '.join(sorted(FILTERS))}")
    return FILTERS[name](**params)


class FilteredStrategy(VectorizedStrategy):
    """Ic stratejinin BUY sinyallerini filtrelerle (VE mantigi) sikilastirir."""

    def __init__(self, inner: Strategy, filters: list[EntryFilter]) -> None:
        self.inner = inner
        self.filters = filters
        self.name = f"{inner.name}+{'+'.join(f.name for f in filters)}" if filters else inner.name
        self.required_lookback = max([inner.required_lookback, *(f.required_lookback for f in filters)])
        self.param_grid = inner.param_grid

    def params(self) -> dict:
        return self.inner.params()

    def generate_signals(self, df):
        codes = self.inner.generate_signals(df).to_numpy().copy()
        for f in self.filters:
            codes = f.transform(df, codes)
        if self.filters:
            allowed = np.ones(len(df), dtype=bool)
            for f in self.filters:
                allowed &= f.mask(df).fillna(False).to_numpy(dtype=bool)
            codes[(codes == 1) & ~allowed] = 0
        return pd.Series(codes, index=df.index, name="signal")
