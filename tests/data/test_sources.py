import numpy as np
import pandas as pd
import pytest

from tradecrm.data.sources.align import align_asof
from tradecrm.data.sources.enrich import LiveEnricher, SourceData, enrich
from tradecrm.data.sources.macro import event_distance_columns, load_events
from tradecrm.strategy.filters import (
    FearGreedFilter,
    FundingFilter,
    MacroBlackoutFilter,
)


def _bars(n=24, start="2026-01-01"):
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=idx)


class TestAlignAsof:
    def test_value_used_only_after_known(self):
        df = _bars(10)
        # 08:00'de bilinen funding; 07:00 acilisli bar 08:00'de kapanir -> o bar kullanabilir.
        known = pd.Series([0.001], index=pd.DatetimeIndex([pd.Timestamp("2026-01-01 08:00", tz="UTC")]))
        out = align_asof(known, df, "1h")
        assert np.isnan(out.iloc[6])  # 06:00 bari 07:00'de kapanir: henuz bilinmiyor
        assert out.iloc[7] == 0.001
        assert out.iloc[9] == 0.001

    def test_empty_series_gives_nan(self):
        assert align_asof(pd.Series(dtype=float), _bars(3), "1h").isna().all()

    def test_causal_under_truncation(self):
        df = _bars(48)
        known = pd.Series(np.arange(6, dtype=float), index=df.index[::8] + pd.Timedelta(minutes=3))
        full = align_asof(known, df, "1h")
        part = align_asof(known, df.iloc[:20], "1h")
        assert part.equals(full.iloc[:20])


class TestMacro:
    def test_fomc_events_loaded_in_utc(self):
        events = load_events()
        assert len(events) >= 30
        # 2026-07-29 14:00 ET (yaz saati) = 18:00 UTC; 2026-01-28 14:00 ET = 19:00 UTC
        assert pd.Timestamp("2026-07-29 18:00", tz="UTC") in events
        assert pd.Timestamp("2026-01-28 19:00", tz="UTC") in events

    def test_event_distances(self):
        df = _bars(6, start="2026-01-28 14:00")  # kapanislar 15:00..20:00 UTC
        ev = pd.DatetimeIndex([pd.Timestamp("2026-01-28 19:00", tz="UTC")])
        cols = event_distance_columns(df, "1h", ev)
        assert cols["hours_to_event"].iloc[0] == pytest.approx(4.0)
        assert cols["hours_since_event"].iloc[5] == pytest.approx(1.0)
        assert np.isinf(cols["hours_since_event"].iloc[0])

    def test_blackout_filter(self):
        df = _bars(10, start="2026-01-28 12:00")
        ev = pd.DatetimeIndex([pd.Timestamp("2026-01-28 19:00", tz="UTC")])
        df = df.join(event_distance_columns(df, "1h", ev))
        mask = MacroBlackoutFilter(hours_before=4, hours_after=2).mask(df)
        # kapanis 13:00 (6s once) izinli; 15:00..20:00 (4s once .. 1s sonra) yasak; 21:00 (2s sonra) izinli
        assert mask.iloc[0]
        assert not mask.iloc[2:8].any()
        assert mask.iloc[8]


class TestSourceFilters:
    def test_funding_filter_blocks_high_funding_allows_nan(self):
        df = _bars(3).assign(funding_rate=[0.0001, 0.001, np.nan])
        assert FundingFilter(max_rate=0.0003).mask(df).tolist() == [True, False, True]

    def test_fng_filter(self):
        df = _bars(3).assign(fng=[50, 85, np.nan])
        assert FearGreedFilter(max_value=80).mask(df).tolist() == [True, False, True]

    def test_missing_column_raises(self):
        with pytest.raises(KeyError):
            FundingFilter().mask(_bars(3))


class TestEnrich:
    def test_enrich_adds_requested_columns_only(self):
        df = _bars(5)
        data = SourceData(fng=pd.Series([40.0], index=df.index[:1]))
        out = enrich(df, "BTC/USDT", "1h", {"fng"}, data)
        assert "fng" in out.columns and "funding_rate" not in out.columns
        assert (out["fng"] == 40.0).all()

    def test_live_enricher_fails_open_and_audits(self, monkeypatch):
        from tradecrm.data.sources import funding

        def boom(self, symbol, days=3):
            raise ConnectionError("binance down")

        monkeypatch.setattr(funding.FundingRateSource, "recent", boom)
        monkeypatch.setattr(funding.FundingRateSource, "__init__", lambda self, **kw: None)

        class _Audit:
            def __init__(self):
                self.entries = []

            def log(self, event, **fields):
                self.entries.append((event, fields))

        audit = _Audit()
        out = LiveEnricher("BTC/USDT", "1h", {"funding"}, audit)(_bars(3))
        assert out["funding_rate"].isna().all()
        assert FundingFilter().mask(out).all()  # NaN -> izin
        assert audit.entries[0][0] == "source_unavailable"


class TestBtcSource:
    @staticmethod
    def _btc(n=200, start="2026-01-01"):
        idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
        close = np.linspace(100, 300, n)
        return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0}, index=idx)

    def test_btc_close_is_the_same_bar_close_no_lookahead(self):
        from tradecrm.data.sources.enrich import btc_known_at

        btc = self._btc(50)
        alt = _bars(50)
        out = enrich(alt, "ETH/USDT", "1h", {"btc"}, SourceData(btc=btc_known_at(btc, "1h")))
        # Alt barinin kapanisinda bilinen BTC degeri = ayni acilisli BTC mumunun kapanisi.
        assert (out["btc_close"].to_numpy() == btc["Close"].to_numpy()).all()

    def test_unclosed_btc_bar_ignored(self):
        from tradecrm.data.sources.enrich import btc_known_at

        btc = self._btc(51)  # son BTC mumu alt verisinden bir bar ileride (henuz kapanmamis)
        alt = _bars(50)
        out = enrich(alt, "ETH/USDT", "1h", {"btc"}, SourceData(btc=btc_known_at(btc, "1h")))
        assert out["btc_close"].iloc[-1] == btc["Close"].iloc[-2]

    def test_btc_trend_filter_causal_and_directional(self):
        from tradecrm.data.sources.enrich import btc_known_at
        from tradecrm.strategy.filters import BtcTrendFilter

        n = 300
        idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
        close = np.concatenate([np.linspace(100, 200, 150), np.linspace(200, 100, 150)])
        btc = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0}, index=idx)
        df = enrich(_bars(n), "ETH/USDT", "1h", {"btc"}, SourceData(btc=btc_known_at(btc, "1h")))
        flt = BtcTrendFilter(ema_length=20)
        m = flt.mask(df).to_numpy()
        assert m[100] and not m[250]  # yukselis: izin, dusus: yok
        for k in (120, 260):
            assert (flt.mask(df.iloc[:k]).to_numpy() == m[:k]).all()

    def test_btc_trend_filter_fails_open_without_data(self):
        from tradecrm.strategy.filters import BtcTrendFilter

        df = _bars(100).assign(btc_close=float("nan"))
        assert BtcTrendFilter().mask(df).all()

    def test_live_enricher_fetches_btc_for_alts(self):
        btc = self._btc(80)

        class _Provider:
            def __init__(self):
                self.calls = []

            def fetch_latest_ohlcv(self, symbol, timeframe="1h", limit=200):
                self.calls.append((symbol, timeframe))
                return btc

        prov = _Provider()
        out = LiveEnricher("ETH/USDT", "1h", {"btc"}, provider=prov)(_bars(80))
        assert prov.calls == [("BTC/USDT", "1h")]
        assert out["btc_close"].iloc[-1] == btc["Close"].iloc[-1]

    def test_live_enricher_uses_own_bars_for_btc(self):
        df = self._btc(60)
        out = LiveEnricher("BTC/USDT", "1h", {"btc"}, provider=object())(df)
        assert (out["btc_close"] == df["Close"]).all()

    def test_live_enricher_btc_failure_is_fail_open(self):
        class _Down:
            def fetch_latest_ohlcv(self, *a, **k):
                raise ConnectionError("down")

        out = LiveEnricher("ETH/USDT", "1h", {"btc"}, provider=_Down())(_bars(10))
        assert out["btc_close"].isna().all()
