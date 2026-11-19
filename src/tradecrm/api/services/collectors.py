"""Panelden "simdi calistir": toplayiciyi arka planda bir kez calistirir.
Zamanlanmis surecten bagimsizdir; ikisi ayni anda calissa bile yazim
idempotenttir (ON CONFLICT DO NOTHING), yalnizca gereksiz istek olur --
bu yuzden aktif bir calisma varken yenisi reddedilir."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from tradecrm.monitor.collectors import active_run, get_spec, run_cycle

logger = logging.getLogger(__name__)


class CollectorBusyError(Exception):
    pass


def _default_factory(name: str, session):
    from tradecrm.data.collectors.derivatives import DerivativesCollector

    if name == "derivatives":
        return DerivativesCollector(session)
    raise KeyError(name)


class CollectorTrigger:
    def __init__(self, session_factory, symbols: list[str], retention_days: int,
                 collector_factory=_default_factory, executor=None) -> None:
        self.session_factory = session_factory
        self.symbols = symbols
        self.retention_days = retention_days
        self.collector_factory = collector_factory
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="collector")
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, name: str) -> None:
        get_spec(name)  # bilinmeyen ad -> KeyError
        with self._lock:
            if name in self._busy:
                raise CollectorBusyError(name)
            with self.session_factory() as s:
                if active_run(s, name) is not None:
                    raise CollectorBusyError(name)
            self._busy.add(name)
        self.executor.submit(self._run, name)

    def _run(self, name: str) -> None:
        try:
            with self.session_factory() as s:
                run_cycle(s, name, self.collector_factory(name, s), self.symbols, self.retention_days,
                          trigger="manual")
        except Exception:
            logger.exception("Elle tetiklenen toplama basarisiz: %s", name)
        finally:
            with self._lock:
                self._busy.discard(name)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
