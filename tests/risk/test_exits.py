import pytest

from tradecrm.risk import exits as ex

CFG = ex.ExitConfig(stop_atr_mult=2.0, take_profit_atr_mult=4.0, breakeven_at_r=1.0, trail_atr_mult=3.0)


def test_initial_levels():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    assert lv.stop == 90.0
    assert lv.initial_stop == 90.0
    assert lv.take_profit == 120.0
    assert lv.risk_per_unit == 10.0


def test_invalid_atr_raises():
    with pytest.raises(ValueError):
        ex.initial_levels(100.0, float("nan"), CFG)


def test_stop_hit_intrabar_fills_at_stop():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    d = ex.check_bar_exit(lv, bar_open=95.0, bar_high=96.0, bar_low=89.0)
    assert d == ex.ExitDecision("stop_loss", 90.0)


def test_gap_below_stop_fills_at_open():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    d = ex.check_bar_exit(lv, bar_open=85.0, bar_high=86.0, bar_low=80.0)
    assert d.fill_price == 85.0


def test_take_profit_hit():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    d = ex.check_bar_exit(lv, bar_open=110.0, bar_high=121.0, bar_low=109.0)
    assert d == ex.ExitDecision("take_profit", 120.0)


def test_both_hit_same_bar_assumes_stop_first():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    d = ex.check_bar_exit(lv, bar_open=100.0, bar_high=125.0, bar_low=85.0)
    assert d.reason == "stop_loss"


def test_breakeven_then_trailing():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    # 1R'ye (110) ulasilmadan stop degismez
    lv = ex.update_on_close(lv, bar_high=108.0, atr_value=5.0, cfg=CFG)
    assert lv.stop == 90.0
    # 110'a ulasti: stop en az entry; trailing 110-15=95 < 100 -> 100
    lv = ex.update_on_close(lv, bar_high=110.0, atr_value=5.0, cfg=CFG)
    assert lv.stop == 100.0
    assert ex.check_bar_exit(lv, 101, 102, 99.0).reason == "breakeven_stop"
    # 130: trailing 130-15=115
    lv = ex.update_on_close(lv, bar_high=130.0, atr_value=5.0, cfg=CFG)
    assert lv.stop == 115.0
    assert ex.check_bar_exit(lv, 120, 121, 114.0).reason == "trailing_stop"


def test_stop_never_moves_down():
    lv = ex.initial_levels(100.0, 5.0, CFG)
    lv = ex.update_on_close(lv, bar_high=130.0, atr_value=5.0, cfg=CFG)
    high_stop = lv.stop
    lv = ex.update_on_close(lv, bar_high=131.0, atr_value=20.0, cfg=CFG)  # ATR patladi
    assert lv.stop >= high_stop


def test_time_stop():
    cfg = ex.ExitConfig(max_bars_held=5)
    assert not ex.time_stop_due(4, cfg)
    assert ex.time_stop_due(5, cfg)
    assert not ex.time_stop_due(1000, ex.ExitConfig())
