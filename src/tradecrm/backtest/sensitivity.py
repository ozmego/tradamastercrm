"""Parametre hassasiyeti: her sayisal parametre tek tek +-%20 kaydirilir.
Saglam bir avantaj komsu parametrelerde de var olmali; sadece tek bir
noktada calisan parametre, gurultuye uydurulmus (overfit) demektir."""

from dataclasses import dataclass

import pandas as pd

from tradecrm.backtest.metrics import compute_metrics
from tradecrm.backtest.simulator import config_from_profile, simulate
from tradecrm.strategy.profiles import Profile


@dataclass
class SensitivityRow:
    param: str
    value: float
    expectancy_pct: float
    profit_factor: float
    n_trades: int


def _perturb(value, factor):
    new = value * factor
    return max(1, round(new)) if isinstance(value, int) and not isinstance(value, bool) else new


def parameter_sensitivity(
    df: pd.DataFrame, profile: Profile, shifts: tuple[float, ...] = (0.8, 1.2), **sim_kwargs
) -> tuple[list[SensitivityRow], float | None]:
    """Satirlar + 'en kotu komsu expectancy / baz expectancy' orani dondurur.
    Baz expectancy <= 0 ise oran anlamsizdir (None)."""
    cfg = config_from_profile(profile, **sim_kwargs)
    base_strategy = profile.build_strategy()
    base_params = base_strategy.params()

    def run(params):
        res = simulate(df, profile.build_strategy(**params), cfg)
        return compute_metrics(res.trades, res.equity)

    base = run({})
    rows = [SensitivityRow("(baz)", 0, base.expectancy_pct, base.profit_factor, base.n_trades)]
    worst = None
    for name, value in base_params.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        for f in shifts:
            new_value = _perturb(value, f)
            if new_value == value:
                continue
            try:
                m = run({name: new_value})
            except ValueError:
                continue
            rows.append(SensitivityRow(name, new_value, m.expectancy_pct, m.profit_factor, m.n_trades))
            if base.expectancy_pct > 0:
                ratio = m.expectancy_pct / base.expectancy_pct
                worst = ratio if worst is None else min(worst, ratio)
    return rows, worst
