"""CLI'in kullandigi ust seviye degerlendirme: bir profili birden cok
sembolde walk-forward + Monte Carlo + hassasiyet + kapi ile olcer."""

from dataclasses import dataclass

import pandas as pd

from tradecrm.backtest.gate import GateResult, evaluate_gate
from tradecrm.backtest.metrics import Metrics, compute_metrics
from tradecrm.backtest.monte_carlo import MonteCarloResult, bootstrap_trades
from tradecrm.backtest.sensitivity import parameter_sensitivity
from tradecrm.backtest.walk_forward import (
    WalkForwardResult,
    split_holdout,
    walk_forward,
)
from tradecrm.strategy.profiles import Portfolio, Profile


@dataclass
class ProfileEvaluation:
    profile: "Profile | Portfolio"
    per_symbol: dict[str, WalkForwardResult]
    pooled: Metrics  # sembol bazli OOS islemlerin birlesimi
    monte_carlo: MonteCarloResult
    sensitivity_worst: float | None
    gate: GateResult


def evaluate_profile(
    datasets: dict[str, pd.DataFrame],
    profile: Profile,
    train_bars: int,
    test_bars: int,
    warmup_bars: int,
    optimize: bool = False,
    holdout_frac: float = 0.3,
    run_sensitivity: bool = True,
    fee_rate: float = 0.001,
    slippage_bps: float = 5.0,
) -> ProfileEvaluation:
    per_symbol: dict[str, WalkForwardResult] = {}
    all_trades = []
    sens_worst: list[float] = []
    for symbol, df in datasets.items():
        dev, _holdout = split_holdout(df, holdout_frac)  # holdout'a DOKUNULMAZ
        wf = walk_forward(
            dev, profile, train_bars=train_bars, test_bars=test_bars, warmup_bars=warmup_bars,
            optimize=optimize, fee_rate=fee_rate, slippage_bps=slippage_bps,
        )
        per_symbol[symbol] = wf
        if len(wf.trades):
            all_trades.append(wf.trades)
        if run_sensitivity:
            _, worst = parameter_sensitivity(dev, profile, fee_rate=fee_rate, slippage_bps=slippage_bps)
            if worst is not None:
                sens_worst.append(worst)

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(columns=["pnl", "pnl_pct"])
    pooled_equity = _pooled_equity(per_symbol)
    pooled = compute_metrics(trades, pooled_equity)
    mc = bootstrap_trades(trades["pnl_pct"].to_numpy() if len(trades) else [])
    symbol_returns = {s: wf.metrics.total_return for s, wf in per_symbol.items()}
    worst = min(sens_worst) if sens_worst else None
    gate = evaluate_gate(pooled, mc, symbol_returns=symbol_returns, sensitivity_worst_ratio=worst)
    return ProfileEvaluation(profile, per_symbol, pooled, mc, worst, gate)


def _pooled_equity(per_symbol: dict[str, WalkForwardResult]) -> pd.Series:
    """Esit agirlikli sembol portfoyu: her sembolun normalize equity egrisinin
    ortalamasi (Sharpe/DD icin)."""
    curves = []
    for wf in per_symbol.values():
        if len(wf.equity):
            curves.append(wf.equity / wf.equity.iloc[0])
    if not curves:
        return pd.Series([1.0])
    joined = pd.concat(curves, axis=1).sort_index().ffill().bfill()
    return joined.mean(axis=1)


def evaluate_portfolio(
    datasets: dict[str, pd.DataFrame],
    portfolio: Portfolio,
    train_bars: int,
    test_bars: int,
    warmup_bars: int,
    optimize: bool = False,
    holdout_frac: float = 0.3,
    fee_rate: float = 0.001,
    slippage_bps: float = 5.0,
    initial_cash: float = 10_000.0,
) -> ProfileEvaluation:
    """Her uye, sermayenin kendi payiyla ayri bir alt hesapta walk-forward
    edilir; sembol bazinda equity'ler toplanir. Islem P&L yuzdeleri toplam
    hesaba gore olceklenir (pnl_pct * agirlik). Hassasiyet analizi uyeler
    icin ayri ayri yapilmalidir (burada yok)."""
    per_symbol: dict[str, WalkForwardResult] = {}
    all_trades = []
    for symbol, df in datasets.items():
        dev, _holdout = split_holdout(df, holdout_frac)
        equities, trades, blocked = [], [], {}
        for profile, weight in portfolio.members:
            wf = walk_forward(
                dev, profile, train_bars=train_bars, test_bars=test_bars, warmup_bars=warmup_bars,
                optimize=optimize, initial_cash=initial_cash * weight, fee_rate=fee_rate,
                slippage_bps=slippage_bps,
            )
            equities.append(wf.equity)
            if len(wf.trades):
                t = wf.trades.copy()
                t["pnl_pct"] = t["pnl_pct"] * weight
                t["member"] = profile.name
                trades.append(t)
            for k, v in wf.blocked.items():
                blocked[f"{profile.name}:{k}"] = blocked.get(f"{profile.name}:{k}", 0) + v
        equity = pd.concat(equities, axis=1).sort_index().ffill().bfill().sum(axis=1)
        sym_trades = pd.concat(trades, ignore_index=True).sort_values("entry_time") if trades else pd.DataFrame(
            columns=["pnl", "pnl_pct", "entry_time"]
        )
        per_symbol[symbol] = WalkForwardResult(portfolio.name, [], sym_trades, equity,
                                               compute_metrics(sym_trades, equity), blocked)
        if len(sym_trades):
            all_trades.append(sym_trades)

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(columns=["pnl", "pnl_pct"])
    pooled = compute_metrics(trades, _pooled_equity(per_symbol))
    mc = bootstrap_trades(trades["pnl_pct"].to_numpy() if len(trades) else [])
    symbol_returns = {s: wf.metrics.total_return for s, wf in per_symbol.items()}
    gate = evaluate_gate(pooled, mc, symbol_returns=symbol_returns)
    return ProfileEvaluation(portfolio, per_symbol, pooled, mc, None, gate)


def evaluate(datasets, target, train_bars, test_bars, warmup_bars, optimize=False, run_sensitivity=True,
             slippage_bps=5.0) -> ProfileEvaluation:
    if isinstance(target, Portfolio):
        return evaluate_portfolio(datasets, target, train_bars, test_bars, warmup_bars, optimize=optimize,
                                  slippage_bps=slippage_bps)
    return evaluate_profile(datasets, target, train_bars, test_bars, warmup_bars, optimize=optimize,
                            run_sensitivity=run_sensitivity, slippage_bps=slippage_bps)
