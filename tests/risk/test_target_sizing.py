from datetime import UTC, datetime

import pytest

from tradecrm.risk.target_sizing import (
    compute_position_fraction,
    elapsed_fraction_of_utc_day,
)


class TestElapsedFractionOfUtcDay:
    def test_midnight_is_zero(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert elapsed_fraction_of_utc_day(dt) == pytest.approx(0.0)

    def test_noon_is_half(self):
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert elapsed_fraction_of_utc_day(dt) == pytest.approx(0.5)

    def test_end_of_day_close_to_one(self):
        dt = datetime(2026, 1, 1, 23, 59, 59, tzinfo=UTC)
        assert elapsed_fraction_of_utc_day(dt) > 0.999


class TestComputePositionFraction:
    def test_invalid_daily_target_raises(self):
        with pytest.raises(ValueError):
            compute_position_fraction(0.0, 0.5, daily_target_pct=0.0)

    def test_invalid_fraction_bounds_raise(self):
        with pytest.raises(ValueError):
            compute_position_fraction(0.0, 0.5, daily_target_pct=0.0075, min_fraction=0.8, max_fraction=0.2)

    def test_day_start_no_pnl_yet_returns_midpoint(self):
        result = compute_position_fraction(
            realized_pnl_pct_today=0.0,
            elapsed_fraction_of_day=0.0,
            daily_target_pct=0.0075,
            min_fraction=0.2,
            max_fraction=1.0,
        )
        assert result.pace_ratio is None
        assert result.fraction_of_pool == pytest.approx((0.2 + 1.0) / 2)

    def test_far_behind_pace_scales_up_to_max(self):
        # Gunun yarisi gecmis, hedefin cok gerisinde (buyuk negatif P&L).
        result = compute_position_fraction(
            realized_pnl_pct_today=-0.05,
            elapsed_fraction_of_day=0.5,
            daily_target_pct=0.0075,
            min_fraction=0.2,
            max_fraction=1.0,
        )
        assert result.fraction_of_pool == pytest.approx(1.0)
        assert result.reason == "behind_target_scale_up"

    def test_far_ahead_of_pace_scales_down_to_min(self):
        result = compute_position_fraction(
            realized_pnl_pct_today=0.05,
            elapsed_fraction_of_day=0.5,
            daily_target_pct=0.0075,
            min_fraction=0.2,
            max_fraction=1.0,
        )
        assert result.fraction_of_pool == pytest.approx(0.2)

    def test_exactly_on_pace_is_midpoint(self):
        result = compute_position_fraction(
            realized_pnl_pct_today=0.00375,  # tam olarak elapsed=0.5 icin beklenen deger
            elapsed_fraction_of_day=0.5,
            daily_target_pct=0.0075,
            min_fraction=0.2,
            max_fraction=1.0,
        )
        assert result.fraction_of_pool == pytest.approx((0.2 + 1.0) / 2)
        assert result.pace_ratio == pytest.approx(1.0)
        assert result.reason == "on_pace"

    def test_stop_on_hit_returns_zero_when_target_reached(self):
        result = compute_position_fraction(
            realized_pnl_pct_today=0.01,
            elapsed_fraction_of_day=0.3,
            daily_target_pct=0.0075,
            stop_on_hit=True,
        )
        assert result.fraction_of_pool == 0.0
        assert result.reason == "daily_target_reached_stop"

    def test_stop_on_hit_false_still_trades_after_target_reached(self):
        result = compute_position_fraction(
            realized_pnl_pct_today=0.01,
            elapsed_fraction_of_day=0.3,
            daily_target_pct=0.0075,
            min_fraction=0.2,
            max_fraction=1.0,
            stop_on_hit=False,
        )
        assert result.fraction_of_pool > 0.0
        assert result.fraction_of_pool < 1.0

    @pytest.mark.parametrize(
        "realized,elapsed",
        [
            (-10.0, 0.99),  # asiri gerisinde
            (10.0, 0.01),  # asiri onunde
            (0.0, 0.0),
            (-0.5, 0.5),
            (0.5, 0.5),
        ],
    )
    def test_output_always_within_hard_bounds(self, realized, elapsed):
        result = compute_position_fraction(
            realized_pnl_pct_today=realized,
            elapsed_fraction_of_day=elapsed,
            daily_target_pct=0.0075,
            min_fraction=0.2,
            max_fraction=1.0,
        )
        assert 0.2 <= result.fraction_of_pool <= 1.0
