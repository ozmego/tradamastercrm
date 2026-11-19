"""Panelden otomatik baslatma gorevleri (Gorev Zamanlayici) -- runner/autostart.py
uzerine ince bir katman: kisa sureli onbellek (her istekte PowerShell
calistirmamak icin) ve test icin degistirilebilir arka uc."""

import threading
import time
from pathlib import Path

from tradecrm.runner import autostart as backend

CACHE_SECONDS = 15.0


class AutostartService:
    def __init__(self, working_dir: Path, impl=backend, clock=time.monotonic) -> None:
        self.working_dir = working_dir
        self.impl = impl
        self.clock = clock
        self._cache: tuple[float, dict] | None = None
        self._lock = threading.Lock()

    @property
    def tasks(self) -> dict:
        return self.impl.TASKS

    def supported(self) -> bool:
        return self.impl.supported()

    def status_all(self) -> dict[str, dict]:
        with self._lock:
            if self._cache and self.clock() - self._cache[0] < CACHE_SECONDS:
                return self._cache[1]
        data = {k: v.as_dict() for k, v in self.impl.status_all().items()}
        with self._lock:
            self._cache = (self.clock(), data)
        return data

    def enable(self, key: str) -> dict:
        st = self.impl.enable(self.working_dir, key)
        self._cache = None
        return st.as_dict()

    def disable(self, key: str) -> bool:
        removed = self.impl.disable(key)
        self._cache = None
        return removed
