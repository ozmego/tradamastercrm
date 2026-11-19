import pytest

from tradecrm.risk.position_sizing import (
    SizingConfig,
    compute_total_fraction,
    risk_based_fraction,
)


def test_risk_based_fraction_loss_at_stop_equals_risk():
    # entry 100, stop 95 -> %5 mesafe; %1 risk -> notional %20
    frac = risk_based_fraction(0.01, 100.0, 95.0, max_fraction=0.75)
    assert frac == pytest.approx(0.20)
    equity = 10_000
    loss_at_stop = equity * frac * (100 - 95) / 100
    assert loss_at_stop == pytest.approx(equity * 0.01)


def test_risk_based_fraction_capped():
    # cok yakin stop -> devasa pozisyon onerir; tavan %75
    assert risk_based_fraction(0.01, 100.0, 99.9, max_fraction=0.75) == 0.75


def test_invalid_stop_gives_zero():
    assert risk_based_fraction(0.01, 100.0, 101.0, max_fraction=0.75) == 0.0


def test_risk_mode_requires_stop():
    with pytest.raises(ValueError):
        compute_total_fraction(SizingConfig(mode="risk"), 100.0, None)


def test_fixed_mode_respects_cap():
    frac, _ = compute_total_fraction(SizingConfig(mode="fixed", fixed_fraction=0.9, max_total_fraction=0.75), 100.0, None)
    assert frac == 0.75


def test_daily_target_mode_bounded_by_cap():
    frac, _ = compute_total_fraction(
        SizingConfig(mode="daily_target"), 100.0, None, realized_pnl_pct_today=-0.5, elapsed_fraction_of_day=0.5
    )
    assert 0 < frac <= 0.75


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        SizingConfig(mode="martingale")
