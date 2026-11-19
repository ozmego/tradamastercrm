from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradecrm.backtest.engine import run_backtest
from tradecrm.backtest.metrics import compute_metrics, max_drawdown
from tradecrm.backtest.monte_carlo import bootstrap_trades
from tradecrm.backtest.simulator import SimConfig, simulate
from tradecrm.backtest.walk_forward import split_holdout, walk_forward
from tradecrm.risk.exits import ExitConfig
from tradecrm.risk.position_sizing import SizingConfig
from tradecrm.strategy.base import VectorizedStrategy
from tradecrm.strategy.profiles import get_profile
from tradecrm.strategy.sma_rsi_crossover import EmaRsiCrossoverStrategy

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "ohlcv_sample.csv"


@pytest.fixture()
def fixture_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURE_PATH, index_col=0, parse_dates=True)


class _ScriptedStrategy(VectorizedStrategy):
    """Belirli barlarda belirli sinyaller ureten deterministik strateji."""

    name = "scripted"

    def __init__(self, codes):
        self.codes = codes

    def generate_signals(self, df):
        return pd.Series(np.array(self.codes[: len(df)], dtype=np.int8), index=df.index)


def _bars(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1.0)


FULL = SizingConfig(mode="fixed", fixed_fraction=1.0, max_total_fraction=1.0)


def test_matches_backtesting_py_without_exits(fixture_df):
    """Cikis katmani yokken, %100 sermaye ve sifir kaymayla backtesting.py
    referans motoruyla ayni sonucu vermeli (dolum kurallari capraz kontrol)."""
    strat = EmaRsiCrossoverStrategy()
    ref = run_backtest(fixture_df, lambda: EmaRsiCrossoverStrategy(), cash=10_000, commission=0.001)
    res = simulate(fixture_df, strat, SimConfig(slippage_bps=0, sizing=FULL))
    assert len(res.trades) == ref.stats["# Trades"]
    assert res.final_equity / 10_000 - 1 == pytest.approx(ref.stats["Return [%]"] / 100, abs=2e-3)


def test_signal_fills_at_next_bar_open():
    df = _bars([[100, 101, 99, 100], [105, 106, 104, 105], [110, 111, 109, 110], [120, 121, 119, 120]])
    res = simulate(df, _ScriptedStrategy([1, 0, -1, 0]), SimConfig(slippage_bps=0, fee_rate=0, sizing=FULL))
    tr = res.trades.iloc[0]
    assert tr.entry_price == 105  # bar 1 acilisi
    assert tr.exit_price == 120  # bar 3 acilisi
    assert tr.exit_reason == "signal"
    assert res.final_equity == pytest.approx(10_000 * 120 / 105)


def test_stop_loss_fills_at_stop_and_pnl_includes_fees():
    rows = [[100, 101, 99, 100]] * 20 + [[100, 100.5, 99.5, 100], [100, 100, 80, 85]]
    df = _bars(rows)
    codes = [0] * 19 + [1, 0, 0]
    cfg = SimConfig(slippage_bps=0, fee_rate=0.001, sizing=FULL, exits=ExitConfig(stop_atr_mult=2.0, take_profit_atr_mult=None))
    res = simulate(df, _ScriptedStrategy(codes), cfg)
    tr = res.trades.iloc[0]
    assert tr.exit_reason == "stop_loss"
    assert tr.exit_price < tr.entry_price
    # P&L = nakit degisimi (komisyonlar dahil)
    assert res.final_equity - 10_000 == pytest.approx(tr.pnl)


def test_no_entries_before_trade_start(fixture_df):
    res = simulate(fixture_df, EmaRsiCrossoverStrategy(), SimConfig(), trade_start=len(fixture_df) - 5)
    assert len(res.trades) == 0


def test_risk_sizing_limits_loss_per_trade():
    rows = [[100, 101, 99, 100]] * 20 + [[100, 100.5, 99.5, 100], [100, 100, 50, 60]]
    df = _bars(rows)
    codes = [0] * 19 + [1, 0, 0]
    cfg = SimConfig(slippage_bps=0, fee_rate=0, sizing=SizingConfig(mode="risk", risk_per_trade_pct=0.01),
                    exits=ExitConfig(take_profit_atr_mult=None))
    res = simulate(df, _ScriptedStrategy(codes), cfg)
    assert res.trades.iloc[0].pnl_pct == pytest.approx(-0.01, abs=1e-6)


def test_metrics_and_drawdown():
    assert max_drawdown([100, 120, 90, 130]) == pytest.approx(0.25)
    trades = pd.DataFrame({"pnl": [10.0, -5.0, 20.0], "pnl_pct": [0.01, -0.005, 0.02]})
    eq = pd.Series([100.0, 110.0, 105.0, 125.0], index=pd.date_range("2026", periods=4, freq="D", tz="UTC"))
    m = compute_metrics(trades, eq)
    assert m.n_trades == 3
    assert m.profit_factor == pytest.approx(30 / 5)
    assert m.win_rate == pytest.approx(2 / 3)


def test_monte_carlo_deterministic_and_bounded():
    pnl = np.array([0.02, -0.01, 0.015, -0.01, 0.005] * 20)
    a = bootstrap_trades(pnl, n_sims=500, seed=1)
    b = bootstrap_trades(pnl, n_sims=500, seed=1)
    assert a == b
    assert 0 <= a.max_dd_p50 <= a.max_dd_p95 <= 1


def test_walk_forward_trades_only_in_test_windows(fixture_df):
    profile = get_profile("guarded")
    wf = walk_forward(fixture_df, profile, train_bars=100, test_bars=50, warmup_bars=60)
    assert len(wf.folds) >= 3
    first_test = wf.folds[0].test_start
    if len(wf.trades):
        assert (wf.trades["entry_time"] >= first_test).all()


def test_split_holdout(fixture_df):
    dev, hold = split_holdout(fixture_df, 0.3)
    assert len(dev) + len(hold) == len(fixture_df)
    assert dev.index[-1] < hold.index[0]


def test_vectorized_signals_are_causal(fixture_df):
    """Lookahead testi: k bara kadar hesaplanan sinyaller, tum veriyle
    hesaplananlarin ilk k elemanina esit olmali."""
    s = EmaRsiCrossoverStrategy()
    full = s.generate_signals(fixture_df).to_numpy()
    for k in (50, 120, 199, 250):
        assert (s.generate_signals(fixture_df.iloc[:k]).to_numpy() == full[:k]).all()


def test_daily_profit_target_blocks_rest_of_day_like_live():
    """Canli ile ayni kural: gunun gerceklesen K/Z'si +%5'e ulasinca o gun yeni giris yok;
    ertesi gun yeniden serbest (kill-switch degil)."""
    from tradecrm.risk.breakers import BreakerConfig

    rows = [[100, 100, 100, 100]] * 30
    rows[2] = rows[3] = [110, 110, 110, 110]  # 1. islem: 100 -> 110 (%10)
    df = _bars(rows)
    codes = [0] * 30
    codes[0], codes[2] = 1, -1  # gun 1: al-sat (+%10)
    codes[5] = 1  # ayni gun ikinci AL -> hedef dolu, engellenmeli
    codes[26] = 1  # ertesi gun (bar 24+ = 2 Ocak) -> serbest
    cfg = SimConfig(slippage_bps=0, fee_rate=0, sizing=FULL,
                    breakers=BreakerConfig(max_consecutive_losses=None, max_weekly_loss_pct=None,
                                           max_drawdown_pct=None, daily_profit_target_pct=0.05))
    res = simulate(df, _ScriptedStrategy(codes), cfg)
    assert res.blocked.get("daily_target_reached", 0) == 1
    entries = list(res.trades["entry_time"])
    assert entries[0] == df.index[1] and entries[1] == df.index[27]
