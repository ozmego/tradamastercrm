"""Oneri 13: performans bozulma (drift) alarmi.

Canli/paper islemlerin ortalama fiyat getirisi, backtest OOS islemlerinden
bootstrap ile turetilen alt guven sinirinin altina duserse strateji
"bozulmus" sayilir: hesapta yeni alim kapatilir (TRADING_DISABLED) ve
bildirim gonderilir. Satis yolu etkilenmez.

Olcu: islem basina fiyat getirisi (cikis / giris - 1). Pozisyon boyutundan
ve hesap paylasimindan (coklu sembol) bagimsizdir; backtest'teki
`return_pct` ile ayni tanim (komisyon haric).

Pencere, hesabin alim izninin SON acildigi andan baslar: bozulma sonrasi
`risk enable-trading` bilincli bir "temiz sayfa" kararidir.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_BASELINE_DIR = Path("state/baselines")


# ------------------------------------------------------------------ baseline
@dataclass(frozen=True)
class Baseline:
    profile: str
    timeframe: str
    symbols: list[str]
    returns: list[float]  # OOS islem basina fiyat getirisi
    created_at: str
    metrics: dict = field(default_factory=dict)
    gate_passed: bool = False

    @property
    def mean(self) -> float:
        return float(np.mean(self.returns)) if self.returns else 0.0


def baseline_path(profile: str, timeframe: str, root: Path | str = DEFAULT_BASELINE_DIR) -> Path:
    safe = profile.replace(":", "_").replace("+", "_").replace("/", "_")
    return Path(root) / f"{safe}__{timeframe}.json"


def save_baseline(baseline: Baseline, root: Path | str = DEFAULT_BASELINE_DIR) -> Path:
    path = baseline_path(baseline.profile, baseline.timeframe, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(baseline), indent=1), encoding="utf-8")
    return path


def load_baseline(profile: str, timeframe: str, root: Path | str = DEFAULT_BASELINE_DIR) -> Baseline | None:
    path = baseline_path(profile, timeframe, root)
    if not path.exists():
        return None
    return Baseline(**json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------- check
@dataclass(frozen=True)
class DriftConfig:
    window: int = 30  # son N islem
    min_trades: int = 10  # bundan azsa karar yok
    alpha: float = 0.05  # tek tarafli; alt %5
    n_boot: int = 5000
    seed: int = 0


_DEFAULT_CONFIG = DriftConfig()


@dataclass(frozen=True)
class DriftResult:
    status: str  # "no_baseline" | "insufficient" | "ok" | "degraded"
    n: int
    live_mean: float | None
    lower_bound: float | None
    baseline_mean: float | None

    def describe(self) -> str:
        if self.status == "no_baseline":
            return "baseline yok (drift izlemesi kapali)"
        if self.status == "insufficient":
            return f"yetersiz islem ({self.n}, karar icin en az gerekli sayiya ulasilmadi)"
        return (
            f"{self.status}: son {self.n} islem ort. {self.live_mean:+.2%} "
            f"(alt sinir {self.lower_bound:+.2%}, backtest ort. {self.baseline_mean:+.2%})"
        )


def bootstrap_lower_bound(baseline_returns, n: int, alpha: float, n_boot: int, seed: int = 0) -> float:
    """Backtest dagilimindan cekilen n islemlik orneklem ortalamalarinin alpha yuzdeligi."""
    arr = np.asarray(baseline_returns, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(arr, size=(n_boot, n), replace=True).mean(axis=1)
    return float(np.quantile(means, alpha))


def check_drift(live_returns, baseline: Baseline | None, config: DriftConfig = _DEFAULT_CONFIG) -> DriftResult:
    live = [float(x) for x in live_returns][-config.window:]
    if baseline is None or len(baseline.returns) < config.min_trades:
        return DriftResult("no_baseline", len(live), None, None, None)
    if len(live) < config.min_trades:
        return DriftResult("insufficient", len(live), None, None, baseline.mean)
    lower = bootstrap_lower_bound(baseline.returns, len(live), config.alpha, config.n_boot, config.seed)
    live_mean = float(np.mean(live))
    return DriftResult("degraded" if live_mean < lower else "ok", len(live), live_mean, lower, baseline.mean)


def trade_return(trade) -> float | None:
    if trade.exit_price is None or not trade.entry_price:
        return None
    return trade.exit_price / trade.entry_price - 1.0


# --------------------------------------------------------------------- guard
class DriftGuard:
    """Scheduler'da runner'lardan sonra calisir (MultiRunner uyesi olarak).
    Hesap basina bir tane: coklu sembolde islemler hesap genelinde toplanir."""

    def __init__(self, state, baseline: Baseline | None, notifier, audit_logger, config: DriftConfig = _DEFAULT_CONFIG):
        self.state = state
        self.baseline = baseline
        self.notifier = notifier
        self.audit_logger = audit_logger
        self.config = config
        self._last_n: int | None = None
        self.last_result: DriftResult | None = None

    def live_returns(self) -> list[float]:
        since = self.state.last_trading_enabled_time() or datetime(1970, 1, 1, tzinfo=UTC)
        trades = self.state.get_closed_trades_between(since, datetime.now(UTC) + timedelta(seconds=1))
        trades = sorted((t for t in trades if t.exit_time is not None), key=lambda t: t.exit_time)
        return [r for t in trades if (r := trade_return(t)) is not None]

    def run_once(self) -> DriftResult:
        result = check_drift(self.live_returns(), self.baseline, self.config)
        self.last_result = result
        if result.n != self._last_n:  # her yeni kapanan islemde bir kez logla
            self.audit_logger.log("drift_check", account=self.state.account, **asdict(result))
            self._last_n = result.n
        if result.status == "degraded" and self.state.is_trading_enabled():
            reason = f"drift: {result.describe()}"
            self.state.set_trading_enabled(False, reason=reason)
            self.audit_logger.log("drift_disabled_trading", account=self.state.account, reason=reason)
            self.notifier.send_message(
                f"TradeCRM [{self.state.account}] PERFORMANS BOZULMASI -- yeni alimlar kapatildi. "
                f"{result.describe()}. Acik pozisyonlarin cikislari calismaya devam eder. "
                f"Inceleme sonrasi: tradecrm risk enable-trading --account {self.state.account}"
            )
        return result

    def safe_tick(self) -> None:
        try:
            self.run_once()
        except Exception as exc:  # izleme hatasi trading'i durdurmamali
            logger.exception("Drift kontrolu basarisiz")
            # Oturum runner'larla paylasilir: DB hatasindan sonra geri alinmazsa siradaki
            # is (equity kaydi, sonraki tick) "PendingRollbackError" ile duser.
            session = getattr(self.state, "session", None)
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    logger.exception("Drift sonrasi rollback basarisiz")
            self.audit_logger.log(
                "drift_check_error", account=self.state.account, error=f"{type(exc).__name__}: {exc}"
            )
