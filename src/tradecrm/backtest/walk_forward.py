"""Walk-forward (kaydirmali) out-of-sample degerlendirme.

Veri: [ ......... gelistirme (%70) ......... | holdout (%30) ]
Gelistirme bolumunde: [egitim N bar | test M bar] pencereleri M bar
kaydirilarak tekrarlanir. Her pencerede (istenirse) parametreler SADECE
egitim verisinde secilir, test verisinde olculur. Sonuc olarak raporlanan
her islem, parametre seciminde hic gorulmemis veride yapilmistir.

Holdout bolumu walk-forward'da KULLANILMAZ; sadece son karar icin, bir kez
(`evaluate_holdout`) calistirilir.
"""

import itertools
from dataclasses import dataclass, field

import pandas as pd

from tradecrm.backtest.metrics import Metrics, compute_metrics
from tradecrm.backtest.simulator import (
    SimConfig,
    SimResult,
    config_from_profile,
    simulate,
)
from tradecrm.strategy.profiles import Profile

DEFAULT_WARMUP_BARS = 600  # EMA200/ADX gibi gostergelerin oturmasi icin


@dataclass
class FoldResult:
    train_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    params: dict
    result: SimResult
    metrics: Metrics


@dataclass
class WalkForwardResult:
    profile_name: str
    folds: list[FoldResult]
    trades: pd.DataFrame
    equity: pd.Series
    metrics: Metrics
    blocked: dict[str, int] = field(default_factory=dict)


def split_holdout(df: pd.DataFrame, holdout_frac: float = 0.3) -> tuple[pd.DataFrame, pd.DataFrame]:
    cut = int(len(df) * (1 - holdout_frac))
    return df.iloc[:cut], df.iloc[cut:]


def _simulate_window(df, start, end, warmup, profile: Profile, cfg: SimConfig, params: dict) -> SimResult:
    lo = max(0, start - warmup)
    window = df.iloc[lo:end]
    strategy = profile.build_strategy(**params)
    return simulate(window, strategy, cfg, trade_start=start - lo)


def _test_portion(result: SimResult, start_ts) -> tuple[pd.DataFrame, pd.Series]:
    trades = result.trades[result.trades["entry_time"] >= start_ts] if len(result.trades) else result.trades
    equity = result.equity[result.equity.index >= start_ts]
    return trades, equity


def _objective(m: Metrics, min_trades: int) -> float:
    """Egitimde parametre secimi: yeterli islem varsa expectancy * sqrt(n)
    (t-istatistigine benzer; az islemle sansli parametreleri cezalandirir)."""
    if m.n_trades < min_trades:
        return float("-inf")
    return m.expectancy_pct * (m.n_trades**0.5)


def optimize_params(
    df, start, end, warmup, profile: Profile, cfg: SimConfig, grid: dict[str, list], min_trades: int = 15
) -> dict:
    if not grid:
        return {}
    keys = list(grid)
    best, best_score = {}, float("-inf")
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo, strict=True))
        try:
            res = _simulate_window(df, start, end, warmup, profile, cfg, params)
        except ValueError:  # gecersiz kombinasyon (orn. fast >= slow)
            continue
        trades, equity = _test_portion(res, df.index[start])
        score = _objective(compute_metrics(trades, equity), min_trades)
        if score > best_score:
            best, best_score = params, score
    return best


def walk_forward(
    df: pd.DataFrame,
    profile: Profile,
    train_bars: int,
    test_bars: int,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    optimize: bool = False,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    slippage_bps: float = 5.0,
) -> WalkForwardResult:
    folds: list[FoldResult] = []
    equity_parts: list[pd.Series] = []
    trade_parts: list[pd.DataFrame] = []
    blocked: dict[str, int] = {}
    cash = initial_cash
    grid = profile.build_strategy().param_grid if optimize else {}

    test_start = train_bars
    while test_start < len(df):
        test_end = min(test_start + test_bars, len(df))
        if test_end - test_start < max(10, test_bars // 4):
            break
        base_cfg = config_from_profile(profile, initial_cash=cash, fee_rate=fee_rate, slippage_bps=slippage_bps)
        params = optimize_params(df, test_start - train_bars, test_start, warmup_bars, profile, base_cfg, grid) if grid else {}

        res = _simulate_window(df, test_start, test_end, warmup_bars, profile, base_cfg, params)
        start_ts = df.index[test_start]
        trades, equity = _test_portion(res, start_ts)
        folds.append(
            FoldResult(
                train_start=df.index[test_start - train_bars],
                test_start=start_ts,
                test_end=df.index[test_end - 1],
                params=params,
                result=res,
                metrics=compute_metrics(trades, equity),
            )
        )
        trade_parts.append(trades)
        equity_parts.append(equity)
        for k, v in res.blocked.items():
            blocked[k] = blocked.get(k, 0) + v
        if len(equity):
            cash = float(equity.iloc[-1])
        test_start = test_end

    trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
    equity = pd.concat(equity_parts) if equity_parts else pd.Series(dtype=float)
    metrics = compute_metrics(trades, equity) if len(equity) else compute_metrics(pd.DataFrame(columns=["pnl", "pnl_pct"]), pd.Series([initial_cash]))
    return WalkForwardResult(profile.name, folds, trades, equity, metrics, blocked)


def evaluate_holdout(
    dev_df: pd.DataFrame,
    holdout_df: pd.DataFrame,
    profile: Profile,
    params: dict | None = None,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    slippage_bps: float = 5.0,
) -> tuple[SimResult, Metrics]:
    """Holdout'u bir kez calistirir. Isinma icin gelistirme verisinin
    sonundan warmup_bars bar eklenir (o barlarda giris yapilmaz)."""
    full = pd.concat([dev_df.iloc[-warmup_bars:], holdout_df])
    cfg = config_from_profile(profile, initial_cash=initial_cash, fee_rate=fee_rate, slippage_bps=slippage_bps)
    res = simulate(full, profile.build_strategy(**(params or {})), cfg, trade_start=min(warmup_bars, len(dev_df)))
    trades, equity = _test_portion(res, holdout_df.index[0])
    return res, compute_metrics(trades, equity)
