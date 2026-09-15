from apscheduler.schedulers.blocking import BlockingScheduler

from tradecrm.runner.live_runner import LiveRunner

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


def timeframe_to_seconds(timeframe: str) -> int:
    if timeframe not in _TIMEFRAME_SECONDS:
        available = ", ".join(sorted(_TIMEFRAME_SECONDS, key=_TIMEFRAME_SECONDS.get))
        raise ValueError(f"Bilinmeyen zaman dilimi: {timeframe!r}. Mevcut: {available}")
    return _TIMEFRAME_SECONDS[timeframe]


def run_scheduler(runner: LiveRunner, timeframe: str, scheduler=None, run_immediately: bool = True) -> None:
    """runner.safe_tick'i timeframe'e karsilik gelen aralikla calistirir.
    scheduler parametresi test edilebilirlik icin disaridan verilebilir
    (varsayilan: bloklayan gercek APScheduler)."""
    scheduler = scheduler or BlockingScheduler(timezone="UTC")
    interval_seconds = timeframe_to_seconds(timeframe)

    if run_immediately:
        runner.safe_tick()

    scheduler.add_job(runner.safe_tick, "interval", seconds=interval_seconds, id="tradecrm_live_tick")
    scheduler.start()
