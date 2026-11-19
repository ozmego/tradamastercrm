"""Uygulama genelindeki bagimliliklar. Testler sahte fiyat kaynagi, sahte
surecler ve test DB oturumu enjekte eder."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

from tradecrm.api.security import LoginThrottle


class CachedMarket:
    """Panel her yenilemede borsaya gitmesin: fiyat ve mum verisi icin kisa TTL'li onbellek."""

    def __init__(self, provider, price_ttl: float = 15.0, ohlcv_ttl: float = 60.0, clock=time.monotonic) -> None:
        self.provider = provider
        self.price_ttl = price_ttl
        self.ohlcv_ttl = ohlcv_ttl
        self.clock = clock
        self._prices: dict[str, tuple[float, float]] = {}
        self._ohlcv: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def fetch_latest_price(self, symbol: str) -> float:
        now = self.clock()
        with self._lock:
            hit = self._prices.get(symbol)
            if hit and now - hit[0] < self.price_ttl:
                return hit[1]
        price = float(self.provider.fetch_latest_price(symbol))
        with self._lock:
            self._prices[symbol] = (now, price)
        return price

    def fetch_latest_ohlcv(self, symbol: str, timeframe: str, limit: int = 200):
        key = (symbol, timeframe, limit)
        now = self.clock()
        with self._lock:
            hit = self._ohlcv.get(key)
            if hit and now - hit[0] < self.ohlcv_ttl:
                return hit[1]
        df = self.provider.fetch_latest_ohlcv(symbol, timeframe=timeframe, limit=limit)
        with self._lock:
            self._ohlcv[key] = (now, df)
        return df


@dataclass
class AppContext:
    session_factory: Callable[[], AbstractContextManager]
    market: object  # fetch_latest_price / fetch_latest_ohlcv
    collector: object  # ManagedProcess benzeri
    live: object  # ManagedProcess benzeri
    # Canli (gercek hesap) runner'lari: {"kucoin": ManagedProcess}. Panel yalnizca durdurur ve
    # SON PARAMETRELERLE yeniden baslatir; parametre/emir salteri panelden degistirilemez.
    exchange_runners: dict = field(default_factory=dict)
    backtests: object | None = None  # BacktestQueue
    session_hours: int = 12
    cookie_secure: bool = False
    audit_log_path: str = "logs/audit.jsonl"
    baseline_dir: Path = Path("state/baselines")
    live_trading_allowed_by_env: bool = False
    static_dir: Path | None = None
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    # Veri toplayici izleme (monitor/collectors.py)
    collect_symbols: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    collect_retention_days: int = 45
    collector_trigger: object | None = None  # CollectorTrigger: panelden "simdi calistir"
    autostart: object | None = None  # AutostartService (Gorev Zamanlayici)
    started_at: str | None = None  # bu panel surecinin baslangici
    notifier: object | None = None  # TelegramNotifier (yapilandirilmissa) -- rapor gonderme
