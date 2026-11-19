import numpy as np
import pandas as pd
import pytest

from tradecrm.backtest.simulator import SimConfig, simulate
from tradecrm.risk import exits as ex
from tradecrm.risk.position_sizing import SizingConfig
from tradecrm.strategy.base import VectorizedStrategy
from tradecrm.strategy.donchian_breakout import (
    DonchianBreakdownStrategy,
    DonchianBreakoutStrategy,
)

FULL = SizingConfig(mode="risk", risk_per_trade_pct=1.0, max_total_fraction=1.0)  # tum nakit (test)


class _Short(VectorizedStrategy):
    name = "scripted_short"
    direction = "short"

    def __init__(self, codes):
        self.codes = codes

    def generate_signals(self, df):
        return pd.Series(np.array(self.codes[: len(df)], dtype=np.int8), index=df.index)


def _bars(rows, freq="h"):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1.0)


def _flat(n, price=100.0):
    return [[price, price + 0.5, price - 0.5, price]] * n


EXITS = ex.ExitConfig(atr_period=3, stop_atr_mult=10.0, take_profit_atr_mult=None, breakeven_at_r=None,
                      trail_atr_mult=None)


def test_short_profits_when_price_falls():
    rows = _flat(6) + [[90, 90.5, 89.5, 90]] * 4
    codes = [0] * 10
    codes[4], codes[7] = 1, -1  # bar 4 kapanisinda short, bar 7'de kapat
    cfg = SimConfig(slippage_bps=0, fee_rate=0, exits=EXITS, sizing=FULL)
    res = simulate(_bars(rows), _Short(codes), cfg)
    [t] = res.trades.itertuples()
    assert t.entry_price == 100 and t.exit_price == 90
    assert t.pnl == pytest.approx((100 - 90) * t.qty) and t.pnl > 0
    assert t.return_pct == pytest.approx(100 / 90 - 1)
    assert res.final_equity == pytest.approx(10_000 + t.pnl)


def test_short_stop_above_entry_and_gap_fills_worse():
    rows = _flat(6) + [[100, 100.5, 99.5, 100], [130, 131, 129, 130], [130, 130, 130, 130]]
    codes = [0] * 9
    codes[5] = 1  # bar 6 acilisinda 100'den short
    tight = ex.ExitConfig(atr_period=3, stop_atr_mult=2.0, take_profit_atr_mult=None, breakeven_at_r=None,
                          trail_atr_mult=None)
    cfg = SimConfig(slippage_bps=0, fee_rate=0, exits=tight, sizing=FULL)
    res = simulate(_bars(rows), _Short(codes), cfg)
    [t] = res.trades.itertuples()
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == 130  # stop (~102) ustunde bosluklu acilis: acilistan (daha kotu) dolar
    assert t.pnl < 0


def test_short_exit_levels_only_move_down():
    cfg = ex.ExitConfig(atr_period=3, stop_atr_mult=2.0, take_profit_atr_mult=None, breakeven_at_r=1.0,
                        trail_atr_mult=1.0)
    lv = ex.short_initial_levels(100.0, 5.0, cfg)
    assert lv.stop == 110.0 and lv.take_profit is None
    lv = ex.short_update_on_close(lv, bar_low=95.0, atr_value=5.0, cfg=cfg)  # 1R'ye (90) ulasmadi
    assert lv.stop == 110.0
    lv = ex.short_update_on_close(lv, bar_low=88.0, atr_value=5.0, cfg=cfg)  # basabas + trailing
    assert lv.stop == pytest.approx(93.0)  # min(entry=100, 88+5)
    lv2 = ex.short_update_on_close(lv, bar_low=99.0, atr_value=5.0, cfg=cfg)  # fiyat geri yukseldi
    assert lv2.stop == pytest.approx(93.0)  # stop asla yukari cekilmez
    assert ex.short_check_bar_exit(lv2, 92.0, 94.0, 91.0).reason == "trailing_stop"


@pytest.mark.parametrize("rate,sign", [(0.001, 1), (-0.001, -1)])
def test_funding_received_when_positive_paid_when_negative(rate, sign):
    rows = _flat(14)
    codes = [0] * 14
    codes[4], codes[11] = 1, -1
    df = _bars(rows, freq="8h").assign(funding_rate=rate)
    cfg = SimConfig(slippage_bps=0, fee_rate=0, exits=EXITS, sizing=FULL)
    [t] = simulate(df, _Short(codes), cfg).trades.itertuples()
    assert np.sign(t.pnl) == sign  # fiyat sabit: K/Z yalnizca fonlamadan
    assert abs(t.pnl) == pytest.approx(t.qty * 100 * abs(rate) * t.bars_held, rel=1e-6)


def test_long_simulator_unchanged_for_long_strategies():
    class _Long(_Short):
        direction = "long"

    rows = _flat(6) + [[110, 110.5, 109.5, 110]] * 4
    codes = [0] * 10
    codes[4], codes[7] = 1, -1
    cfg = SimConfig(slippage_bps=0, fee_rate=0, exits=EXITS, sizing=FULL)
    [t] = simulate(_bars(rows), _Long(codes), cfg).trades.itertuples()
    assert t.exit_price == 110 and t.pnl > 0  # long: fiyat yukselince kar


def test_breakdown_is_exact_mirror_of_breakout():
    rng = np.random.default_rng(5)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 600)))
    high, low = close * 1.01, close * 0.99
    idx = pd.date_range("2025-01-01", periods=600, freq="D", tz="UTC")
    df = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": 1.0}, index=idx)
    mirror = pd.DataFrame({"Open": -close, "High": -low, "Low": -high, "Close": -close, "Volume": 1.0}, index=idx)
    down = DonchianBreakdownStrategy(40, 20).generate_signals(df).to_numpy()
    up = DonchianBreakoutStrategy(40, 20).generate_signals(mirror).to_numpy()
    assert (down == up).all() and (down == 1).sum() > 0
