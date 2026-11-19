"""Strateji onay kapisi (Oneri 0). Bir profil canliya/paper'a ancak bu
kapiyi gecerse aday olur. Esikler bilerek muhafazakar."""

from dataclasses import dataclass, field

from tradecrm.backtest.metrics import Metrics
from tradecrm.backtest.monte_carlo import MonteCarloResult


@dataclass(frozen=True)
class GateConfig:
    min_trades: int = 100
    min_profit_factor: float = 1.3
    min_sharpe: float = 1.0
    max_mc_dd_p95: float = 0.15
    min_symbol_pass_ratio: float = 2 / 3
    max_sensitivity_degradation: float = 0.5  # +-%20 parametrede expectancy en fazla %50 dusebilir


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def evaluate_gate(
    oos: Metrics,
    mc: MonteCarloResult,
    symbol_returns: dict[str, float] | None = None,
    sensitivity_worst_ratio: float | None = None,
    cfg: GateConfig | None = None,
) -> GateResult:
    cfg = cfg or GateConfig()
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks[name] = ok
        if not ok:
            failures.append(detail)

    check("min_trades", oos.n_trades >= cfg.min_trades, f"OOS islem sayisi {oos.n_trades} < {cfg.min_trades}")
    check(
        "profit_factor",
        oos.profit_factor > cfg.min_profit_factor,
        f"OOS profit factor {oos.profit_factor:.2f} <= {cfg.min_profit_factor}",
    )
    check("sharpe", oos.sharpe > cfg.min_sharpe, f"OOS Sharpe {oos.sharpe:.2f} <= {cfg.min_sharpe}")
    check(
        "mc_drawdown",
        mc.max_dd_p95 < cfg.max_mc_dd_p95,
        f"Monte Carlo p95 drawdown {mc.max_dd_p95:.1%} >= {cfg.max_mc_dd_p95:.0%}",
    )
    if symbol_returns:
        ratio = sum(1 for r in symbol_returns.values() if r > 0) / len(symbol_returns)
        check(
            "symbols",
            ratio >= cfg.min_symbol_pass_ratio - 1e-9,
            f"Pozitif sembol orani {ratio:.0%} < {cfg.min_symbol_pass_ratio:.0%}",
        )
    if sensitivity_worst_ratio is not None:
        check(
            "sensitivity",
            sensitivity_worst_ratio >= 1 - cfg.max_sensitivity_degradation,
            f"Parametre hassasiyeti: en kotu komsu expectancy orani {sensitivity_worst_ratio:.2f}",
        )

    return GateResult(passed=not failures, failures=failures, checks=checks)
