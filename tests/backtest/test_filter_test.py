import numpy as np
import pandas as pd

from tradecrm.backtest.filter_test import (
    attribute_filter,
    permutation_p_value,
    signal_positions,
)
from tradecrm.strategy.filters import EntryFilter


class _MaskFilter(EntryFilter):
    name = "m"

    def __init__(self, mask):
        self._mask = mask

    def mask(self, df):
        return pd.Series(self._mask, index=df.index)


def _df(n=50):
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=idx)


def test_signal_position_is_bar_before_entry():
    df = _df()
    assert signal_positions(df, pd.Series([df.index[5], df.index[9]])).tolist() == [4, 8]


def test_filter_that_removes_the_losers_is_significant():
    df = _df(200)
    entries = np.arange(1, 200, 2)  # 100 islem
    rets = np.where(np.arange(100) < 20, -0.05, 0.03) + np.random.default_rng(1).normal(0, 0.005, 100)
    mask = np.ones(200, dtype=bool)
    mask[entries[:20] - 1] = False  # kaybedenlerin sinyal barlari engelli
    trades = pd.DataFrame({"entry_time": df.index[entries], "return_pct": rets})
    att = attribute_filter({"X": df}, {"X": trades}, _MaskFilter(mask), n_perm=5000)
    assert att.n_removed == 20 and att.n_kept == 80
    assert att.removed_mean < att.kept_mean
    assert att.p_value < 0.001


def test_random_filter_is_not_significant():
    rng = np.random.default_rng(7)
    rets = rng.normal(0.01, 0.05, 120)
    removed = rng.random(120) < 0.25
    assert permutation_p_value(rets, removed, n_perm=5000) > 0.05


def test_filter_that_removes_nothing_has_p_one():
    rets = np.array([0.1, -0.1, 0.2])
    assert permutation_p_value(rets, np.zeros(3, dtype=bool)) == 1.0
