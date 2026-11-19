from datetime import UTC, datetime

import pytest

from tradecrm.runner.scheduler import (
    next_candle_close,
    run_scheduler,
    timeframe_to_seconds,
)


class TestTimeframeToSeconds:
    @pytest.mark.parametrize(
        "timeframe,expected",
        [("1m", 60), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)],
    )
    def test_known_timeframes(self, timeframe, expected):
        assert timeframe_to_seconds(timeframe) == expected

    def test_unknown_timeframe_raises(self):
        with pytest.raises(ValueError):
            timeframe_to_seconds("7x")


class _StubScheduler:
    def __init__(self):
        self.jobs: list = []
        self.started = False

    def add_job(self, func, trigger, seconds=None, id=None, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "seconds": seconds, "id": id, **kwargs})

    def start(self):
        self.started = True


class _StubRunner:
    def __init__(self):
        self.tick_count = 0

    def safe_tick(self):
        self.tick_count += 1


class TestRunScheduler:
    def test_run_immediately_ticks_once_before_scheduling(self):
        runner = _StubRunner()
        scheduler = _StubScheduler()

        run_scheduler(runner, "1h", scheduler=scheduler, run_immediately=True)

        assert runner.tick_count == 1
        assert scheduler.started is True

    def test_skips_immediate_tick_when_disabled(self):
        runner = _StubRunner()
        scheduler = _StubScheduler()

        run_scheduler(runner, "1h", scheduler=scheduler, run_immediately=False)

        assert runner.tick_count == 0

    def test_job_scheduled_with_correct_interval(self):
        runner = _StubRunner()
        scheduler = _StubScheduler()

        run_scheduler(runner, "4h", scheduler=scheduler, run_immediately=False)

        assert len(scheduler.jobs) == 1
        assert scheduler.jobs[0]["seconds"] == 14400
        assert scheduler.jobs[0]["func"] == runner.safe_tick


class TestCandleAlignment:
    def test_next_close_1h(self):
        now = datetime(2026, 1, 1, 10, 17, 3, tzinfo=UTC)
        assert next_candle_close("1h", now=now, offset_seconds=5) == datetime(2026, 1, 1, 11, 0, 5, tzinfo=UTC)

    def test_next_close_4h(self):
        now = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
        assert next_candle_close("4h", now=now, offset_seconds=0) == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_job_start_date_is_aligned(self):
        runner = _StubRunner()
        scheduler = _StubScheduler()
        now = datetime(2026, 1, 1, 10, 17, 3, tzinfo=UTC)
        run_scheduler(runner, "1h", scheduler=scheduler, run_immediately=False, now=now)
        assert scheduler.jobs[0]["start_date"] == datetime(2026, 1, 1, 11, 0, 5, tzinfo=UTC)
