"""Backtest degerlendirmesi icin ortak giris noktasi: CLI ve admin paneli
ayni kodu kullanir.

Bilerek HOLDOUT yok: holdout yalnizca son karar icin bir kez, CLI'dan
bilincli olarak calistirilir (bkz. walk_forward.evaluate_holdout).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,15}/USDT$")
MAX_SYMBOLS = 20


class NoDataError(RuntimeError):
    pass


class InvalidParamsError(ValueError):
    pass


def bars(days: int, timeframe: str) -> int:
    from tradecrm.runner.scheduler import timeframe_to_seconds

    return int(days * 86400 / timeframe_to_seconds(timeframe))


def validate_symbols(symbols: list[str]) -> list[str]:
    cleaned = [s.strip().upper() for s in symbols if s.strip()]
    if not cleaned:
        raise InvalidParamsError("en az bir sembol gerekli")
    if len(cleaned) > MAX_SYMBOLS:
        raise InvalidParamsError(f"en fazla {MAX_SYMBOLS} sembol")
    bad = [s for s in cleaned if not SYMBOL_RE.match(s)]
    if bad:
        raise InvalidParamsError(f"gecersiz sembol: {', '.join(bad)} (beklenen bicim: BTC/USDT)")
    if len(set(cleaned)) != len(cleaned):
        raise InvalidParamsError("ayni sembol birden fazla kez verildi")
    return cleaned


def validate_timeframe(timeframe: str) -> str:
    from tradecrm.runner.scheduler import _TIMEFRAME_SECONDS

    if timeframe not in _TIMEFRAME_SECONDS:
        raise InvalidParamsError(f"gecersiz zaman dilimi: {timeframe}")
    return timeframe


def validate_profile(name: str):
    from tradecrm.strategy.profiles import resolve

    try:
        return resolve(name)
    except (KeyError, ValueError) as exc:
        raise InvalidParamsError(str(exc).strip("'\"")) from None


def load_datasets(
    symbols: list[str], timeframe: str, days: int, sources: set[str] | None = None,
    log: Callable[[str], None] = lambda _msg: None,
) -> dict[str, pd.DataFrame]:
    """OHLCV'yi yukler (data_cache onbellegiyle); sources verilirse ek veri
    kaynaklariyla zenginlestirir (funding/fng/macro -- bkz. data/sources/enrich.py)."""
    from tradecrm.data.cache import load_or_fetch
    from tradecrm.data.provider import CCXTDataProvider
    from tradecrm.data.sources.enrich import enrich, load_historical

    provider = CCXTDataProvider()
    since = datetime.now(UTC) - timedelta(days=days)
    datasets = {}
    for sym in symbols:
        df = load_or_fetch(provider, sym, timeframe, since)
        if df.empty:
            log(f"{sym}: veri cekilemedi, atlaniyor.")
            continue
        datasets[sym] = df
        log(f"{sym}: {len(df)} bar ({df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d})")
    if not datasets:
        raise NoDataError("hic veri yok")
    if sources:
        log(f"Ek veri kaynaklari: {', '.join(sorted(sources))}")
        data = load_historical(list(datasets), sources, since, timeframe=timeframe,
                               btc_ohlcv=datasets.get("BTC/USDT"))
        datasets = {sym: enrich(df, sym, timeframe, sources, data) for sym, df in datasets.items()}
    return datasets


@dataclass(frozen=True)
class BacktestParams:
    profile: str
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    timeframe: str = "4h"
    days: int = 1095
    train_days: int = 180
    test_days: int = 60
    optimize: bool = False
    sensitivity: bool = False
    slippage_bps: float = 5.0

    def validated(self) -> BacktestParams:
        validate_profile(self.profile)
        symbols = validate_symbols(list(self.symbols))
        validate_timeframe(self.timeframe)
        if not (30 <= self.days <= 3650):
            raise InvalidParamsError("gun sayisi 30-3650 arasinda olmali")
        if not (7 <= self.train_days < self.days) or not (7 <= self.test_days < self.days):
            raise InvalidParamsError("egitim/test pencereleri 7 gunden kisa ya da toplamdan uzun olamaz")
        if not (0 <= self.slippage_bps <= 100):
            raise InvalidParamsError("kayma 0-100 bps arasinda olmali")
        return BacktestParams(**{**asdict(self), "symbols": symbols})


def run_evaluation(params: BacktestParams, loader=load_datasets, log: Callable[[str], None] = lambda _m: None):
    """(ProfileEvaluation, sonuc sozlugu) dondurur."""
    from tradecrm.backtest.evaluation import evaluate
    from tradecrm.backtest.walk_forward import DEFAULT_WARMUP_BARS

    params = params.validated()
    target = validate_profile(params.profile)
    datasets = loader(params.symbols, params.timeframe, params.days, target.required_sources, log=log)
    ev = evaluate(
        datasets, target, train_bars=bars(params.train_days, params.timeframe),
        test_bars=bars(params.test_days, params.timeframe), warmup_bars=DEFAULT_WARMUP_BARS,
        optimize=params.optimize, run_sensitivity=params.sensitivity, slippage_bps=params.slippage_bps,
    )
    return ev, evaluation_to_dict(ev, params)


# ------------------------------------------------------------- serilestirme
def _num(x):
    """JSON icin: NaN/inf -> None, numpy -> python."""
    if x is None:
        return None
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return x if math.isfinite(x) else None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (pd.Timestamp, datetime)):
        return pd.Timestamp(x).isoformat()
    return x


def _clean(d: dict) -> dict:
    return {k: _num(v) for k, v in d.items()}


def _downsample(series: pd.Series, max_points: int = 500) -> list[dict]:
    if len(series) == 0:
        return []
    step = max(1, math.ceil(len(series) / max_points))
    s = series.iloc[::step]
    if s.index[-1] != series.index[-1]:
        s = pd.concat([s, series.iloc[[-1]]])
    return [{"t": pd.Timestamp(t).isoformat(), "v": _num(v)} for t, v in s.items()]


def evaluation_to_dict(ev, params: BacktestParams | None = None, max_trades: int = 2000) -> dict:
    from tradecrm.backtest.evaluation import _pooled_equity

    trade_rows = []
    returns: list[float] = []
    for sym, wf in ev.per_symbol.items():
        if not len(wf.trades):
            continue
        for rec in wf.trades.to_dict("records"):
            returns.append(float(rec.get("return_pct", 0.0)))
            trade_rows.append({"symbol": sym, **_clean({
                k: rec.get(k) for k in ("entry_time", "exit_time", "entry_price", "exit_price", "pnl_pct",
                                        "return_pct", "bars_held", "exit_reason")
            })})
    trade_rows.sort(key=lambda r: r.get("entry_time") or "")
    return {
        "profile": ev.profile.name,
        "description": ev.profile.description,
        "params": asdict(params) if params else None,
        "per_symbol": [
            {"symbol": sym, "metrics": _clean(wf.metrics.as_dict()), "blocked": wf.blocked}
            for sym, wf in ev.per_symbol.items()
        ],
        "pooled": _clean(ev.pooled.as_dict()),
        "monte_carlo": _clean(ev.monte_carlo.as_dict()),
        "sensitivity_worst": _num(ev.sensitivity_worst),
        "gate": {"passed": ev.gate.passed, "checks": ev.gate.checks, "failures": ev.gate.failures},
        "equity": _downsample(_pooled_equity(ev.per_symbol)),
        "trades": trade_rows[-max_trades:],
        "n_trades_total": len(trade_rows),
        "returns": returns,  # baseline kaydi icin (drift alarmi)
    }
