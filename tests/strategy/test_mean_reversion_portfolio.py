import numpy as np
import pandas as pd
import pytest

from tradecrm.strategy.mean_reversion import Rsi2MeanReversionStrategy
from tradecrm.strategy.profiles import Portfolio, get_profile, resolve
from tradecrm.strategy.regime import classify_regime


def _random_walk(n=1500, seed=5):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"Open": close, "High": close * 1.003, "Low": close * 0.997, "Close": close,
                         "Volume": 1.0}, index=idx)


def test_rsi2_buys_after_sharp_drop_and_exits_above_sma():
    close = [100.0] * 30 + [99, 97, 94, 90] + [92, 95, 99]
    idx = pd.date_range("2025-01-01", periods=len(close), freq="h", tz="UTC")
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0}, index=idx)
    sig = Rsi2MeanReversionStrategy().generate_signals(df).to_numpy()
    assert 1 in sig[30:34]  # dususte asiri satim -> BUY
    assert sig[-1] == -1  # SMA(5) ustune donus -> SELL


@pytest.mark.parametrize("cls_or_fn", ["strategy", "regime"])
def test_causal(cls_or_fn):
    df = _random_walk()
    if cls_or_fn == "strategy":
        f = lambda d: Rsi2MeanReversionStrategy().generate_signals(d).to_numpy()
    else:
        f = lambda d: classify_regime(d, vol_window=200).to_numpy()
    full = f(df)
    for k in (300, 901):
        assert (f(df.iloc[:k]) == full[:k]).all()


def test_regime_values():
    reg = classify_regime(_random_walk(), vol_window=200)
    assert set(reg.unique()) <= {"trend", "range", "neutral", "high_vol"}


class TestResolve:
    def test_single_profile(self):
        assert resolve("donchian") is get_profile("donchian")

    def test_equal_weight_portfolio(self):
        p = resolve("donchian+mean_reversion")
        assert isinstance(p, Portfolio)
        assert [w for _, w in p.members] == [0.5, 0.5]

    def test_weighted_portfolio(self):
        p = resolve("donchian:0.7+mean_reversion:0.3")
        assert [w for _, w in p.members] == [0.7, 0.3]

    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError):
            resolve("donchian:0.7+mean_reversion:0.7")


def test_portfolio_equity_is_sum_of_members():
    from tradecrm.backtest.evaluation import evaluate_portfolio
    from tradecrm.backtest.walk_forward import split_holdout, walk_forward

    df = _random_walk(2500)
    p = resolve("donchian:0.6+mean_reversion:0.4")
    ev = evaluate_portfolio({"X": df}, p, train_bars=500, test_bars=250, warmup_bars=300)
    dev, _ = split_holdout(df, 0.3)
    a = walk_forward(dev, get_profile("donchian"), 500, 250, 300, initial_cash=6_000)
    b = walk_forward(dev, get_profile("mean_reversion"), 500, 250, 300, initial_cash=4_000)
    combined = ev.per_symbol["X"].equity
    assert combined.iloc[-1] == pytest.approx(a.equity.iloc[-1] + b.equity.iloc[-1])
    assert len(ev.per_symbol["X"].trades) == len(a.trades) + len(b.trades)
