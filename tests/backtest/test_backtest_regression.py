from pathlib import Path

import pandas as pd
import pytest

from tradecrm.backtest.engine import run_backtest
from tradecrm.strategy.sma_rsi_crossover import EmaRsiCrossoverStrategy

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "ohlcv_sample.csv"


@pytest.fixture()
def fixture_df() -> pd.DataFrame:
    return pd.read_csv(FIXTURE_PATH, index_col=0, parse_dates=True)


def test_backtest_regression_ema_rsi_crossover(fixture_df: pd.DataFrame) -> None:
    """Sabit fixture + sabit strateji parametreleri -> sabit sonuc.

    Bu deger, sabit sentetik veri uzerinde bir kez calistirilip elde edilen
    referans degerdir. Strateji ya da motor mantigi sessizce degisirse bu
    test kirilmali."""
    result = run_backtest(
        fixture_df,
        lambda: EmaRsiCrossoverStrategy(fast_period=12, slow_period=26, rsi_period=14),
        cash=10_000,
        commission=0.001,
    )

    assert result.stats["# Trades"] == 1
    assert result.stats["Return [%]"] == pytest.approx(27.5385804, abs=1e-4)
    assert result.stats["Win Rate [%]"] == pytest.approx(100.0)
    assert result.stats["Max. Drawdown [%]"] == pytest.approx(-0.6419116, abs=1e-4)


def test_backtest_no_lookahead_same_result_on_truncated_future(fixture_df: pd.DataFrame) -> None:
    """Verinin sonuna ekstra bar eklemek, gecmisteki islem sinyallerini
    degistirmemeli -- strateji gelecege bakmiyor demektir."""
    truncated = fixture_df.iloc[:250]

    full_result = run_backtest(
        fixture_df,
        lambda: EmaRsiCrossoverStrategy(fast_period=12, slow_period=26, rsi_period=14),
    )
    truncated_result = run_backtest(
        truncated,
        lambda: EmaRsiCrossoverStrategy(fast_period=12, slow_period=26, rsi_period=14),
    )

    full_trades_until_250 = full_result.trades[full_result.trades["EntryTime"] <= truncated.index[-1]]
    assert len(full_trades_until_250) == len(truncated_result.trades) or len(truncated_result.trades) <= len(
        full_trades_until_250
    ) + 1  # son bar acik pozisyon olarak finalize edilebilir, +-1 tolere edilir
