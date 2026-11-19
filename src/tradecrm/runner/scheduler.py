from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}

# Mum kapandiktan sonra borsanin son mumu kesinlestirmesi icin beklenen pay.
DEFAULT_CLOSE_OFFSET_SECONDS = 5


def timeframe_to_seconds(timeframe: str) -> int:
    if timeframe not in _TIMEFRAME_SECONDS:
        available = ", ".join(sorted(_TIMEFRAME_SECONDS, key=_TIMEFRAME_SECONDS.get))
        raise ValueError(f"Bilinmeyen zaman dilimi: {timeframe!r}. Mevcut: {available}")
    return _TIMEFRAME_SECONDS[timeframe]


def next_candle_close(timeframe: str, now: datetime | None = None, offset_seconds: int = DEFAULT_CLOSE_OFFSET_SECONDS) -> datetime:
    """now'dan sonraki ilk mum kapanisi (+offset). Mumlar UTC epoch'a
    hizalidir (Binance: 1h mumlar hh:00'da, 4h mumlar 00/04/08.. UTC)."""
    now = now or datetime.now(UTC)
    interval = timeframe_to_seconds(timeframe)
    epoch_seconds = int(now.timestamp())
    next_boundary = (epoch_seconds // interval + 1) * interval
    return datetime.fromtimestamp(next_boundary, tz=UTC) + timedelta(seconds=offset_seconds)


def run_scheduler(
    runner, timeframe: str, scheduler=None, run_immediately: bool = True, now: datetime | None = None,
    extra_jobs: list[tuple] | None = None,
) -> None:
    """runner.safe_tick'i her mum kapanisindan hemen sonra calistirir.
    Interval surecin basladigi ana degil, mum sinirlarina hizalidir --
    boylece sinyal her zaman yeni kapanmis mumla uretilir."""
    scheduler = scheduler or BlockingScheduler(timezone="UTC")
    interval_seconds = timeframe_to_seconds(timeframe)

    if run_immediately:
        runner.safe_tick()

    scheduler.add_job(
        runner.safe_tick,
        "interval",
        seconds=interval_seconds,
        id="tradecrm_live_tick",
        start_date=next_candle_close(timeframe, now=now),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=interval_seconds // 2,
    )
    # (func, trigger, trigger_kwargs) -- orn. haftalik rapor: (f, "cron", {"day_of_week": "mon", "hour": 8})
    for i, (func, trigger, kwargs) in enumerate(extra_jobs or []):
        scheduler.add_job(func, trigger, id=f"tradecrm_extra_{i}", coalesce=True, max_instances=1, **kwargs)
    scheduler.start()
