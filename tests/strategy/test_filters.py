import numpy as np
import pandas as pd
import pytest

from tradecrm.strategy.base import VectorizedStrategy
from tradecrm.strategy.donchian_breakout import DonchianBreakoutStrategy
from tradecrm.strategy.filters import (
    AdxFilter,
    ConfirmationFilter,
    FilteredStrategy,
    HigherTimeframeTrendFilter,
    TrendFilter,
    VolatilityCapFilter,
    VolumeFilter,
)
from tradecrm.strategy.profiles import PROFILES


def _random_walk(n=1500, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    high = close * (1 + rng.uniform(0, 0.005, n))
    low = close * (1 - rng.uniform(0, 0.005, n))
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.uniform(1, 10, n)}, index=idx)


ALL_FILTERS = [
    TrendFilter(ema_length=50, slope_bars=10),
    AdxFilter(),
    HigherTimeframeTrendFilter("1h", "4h", ema_length=20),
    VolumeFilter(),
    VolatilityCapFilter(window=200),
]


@pytest.mark.parametrize("flt", ALL_FILTERS, ids=lambda f: f.name)
def test_filter_masks_are_causal(flt):
    df = _random_walk()
    full = flt.mask(df).fillna(False).to_numpy(dtype=bool)
    for k in (400, 777, 1201):
        partial = flt.mask(df.iloc[:k]).fillna(False).to_numpy(dtype=bool)
        assert (partial == full[:k]).all(), f"{flt.name} gelecege bakiyor (k={k})"


def test_htf_filter_uses_only_closed_htf_bars():
    """1h barlarda 4h kosulu, 4h mumu kapanmadan degisemez: 4h mumunun
    icindeki barlarda deger, bir onceki kapanmis 4h mumundan gelir."""
    df = _random_walk(400)
    flt = HigherTimeframeTrendFilter("1h", "4h", ema_length=5)
    mask = flt.mask(df)
    # 4h blogu icinde (00:00-02:00 acilisli 1h barlar) deger sabit olmali;
    # deger ancak blogun son 1h bari (03:00, kapanis 04:00) ile degisebilir.
    for block_start in pd.date_range(df.index[40], df.index[-8], freq="4h"):
        inner = mask.loc[block_start : block_start + pd.Timedelta(hours=2)]
        assert inner.nunique() <= 1


def test_filtered_strategy_never_suppresses_sell():
    class _AlwaysSellOrBuy(VectorizedStrategy):
        name = "alt"

        def generate_signals(self, df):
            codes = np.where(np.arange(len(df)) % 2 == 0, 1, -1).astype(np.int8)
            return pd.Series(codes, index=df.index)

    class _BlockAll(TrendFilter):
        def mask(self, df):
            return pd.Series(False, index=df.index)

    df = _random_walk(50)
    sig = FilteredStrategy(_AlwaysSellOrBuy(), [_BlockAll()]).generate_signals(df).to_numpy()
    assert (sig[1::2] == -1).all()
    assert (sig[0::2] == 0).all()


def test_donchian_signals():
    idx = pd.date_range("2025-01-01", periods=8, freq="h", tz="UTC")
    close = [10, 11, 10, 11, 13, 12, 9, 10]
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0}, index=idx)
    sig = DonchianBreakoutStrategy(entry_period=3, exit_period=2).generate_signals(df).to_numpy()
    assert sig[4] == 1  # 13 > max(10, 11, 10... onceki 3 bar) = 11
    assert sig[6] == -1  # 9 < min(13, 12)


def test_donchian_causal():
    df = _random_walk()
    s = DonchianBreakoutStrategy()
    full = s.generate_signals(df).to_numpy()
    assert (s.generate_signals(df.iloc[:900]).to_numpy() == full[:900]).all()


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_profile_builds_and_runs(name):
    from tradecrm.backtest.simulator import config_from_profile, simulate
    from tradecrm.data.sources.enrich import SourceData, enrich

    profile = PROFILES[name]
    df = _random_walk(1200)
    data = SourceData(
        funding={"X": pd.Series(0.0001, index=df.index[::8])},
        fng=pd.Series(50.0, index=df.index[::24]),
        macro_events=pd.DatetimeIndex([df.index[500]]),
    )
    df = enrich(df, "X", "1h", profile.required_sources, data)
    res = simulate(df, profile.build_strategy(), config_from_profile(profile))
    assert len(res.equity) == len(df)
    assert np.isfinite(res.equity.to_numpy()).all()


def _fixed_signals(codes):
    class _Fixed(VectorizedStrategy):
        name = "fixed"

        def generate_signals(self, df):
            return pd.Series(np.asarray(codes, dtype=np.int8), index=df.index)

    return _Fixed()


def _closes(close):
    idx = pd.date_range("2025-01-01", periods=len(close), freq="h", tz="UTC")
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0}, index=idx)


def test_confirmation_delays_buy_and_requires_follow_through():
    df = _closes([10, 11, 12, 11, 12, 13])
    #             BUY@1 -> 12>11 teyit @2 ; BUY@3 -> 12>11 teyit @4 ; BUY@4 -> 13>12 teyit @5
    strat = FilteredStrategy(_fixed_signals([0, 1, 0, 1, 1, 0]), [ConfirmationFilter()])
    assert strat.generate_signals(df).tolist() == [0, 0, 1, 0, 1, 1]


def test_confirmation_rejects_failed_breakout_and_keeps_sell():
    df = _closes([10, 12, 11, 11, 10])
    strat = FilteredStrategy(_fixed_signals([0, 1, 0, 1, -1]), [ConfirmationFilter()])
    assert strat.generate_signals(df).tolist() == [0, 0, 0, 0, -1]


def test_confirmation_is_causal():
    df = _random_walk()
    s = FilteredStrategy(DonchianBreakoutStrategy(entry_period=20, exit_period=10), [ConfirmationFilter()])
    full = s.generate_signals(df).to_numpy()
    for k in (300, 901):
        assert (s.generate_signals(df.iloc[:k]).to_numpy() == full[:k]).all()


def test_confirmation_attribution_matches_transform():
    df = _random_walk(600)
    inner = DonchianBreakoutStrategy(entry_period=20, exit_period=10)
    raw = inner.generate_signals(df).to_numpy()
    pos = np.flatnonzero(raw[:-1] == 1)
    allowed = ConfirmationFilter().allows_trade(df, pos)
    confirmed = FilteredStrategy(inner, [ConfirmationFilter()]).generate_signals(df).to_numpy()
    sells = raw[pos + 1] == -1
    assert ((confirmed[pos + 1] == 1) == (allowed & ~sells)).all()
