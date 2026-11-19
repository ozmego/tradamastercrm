import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    n_trades: int
    win_rate: float
    profit_factor: float
    expectancy_pct: float  # islem basina ortalama P&L (hesap bakiyesine gore)
    avg_win_pct: float
    avg_loss_pct: float
    total_return: float
    cagr: float
    max_drawdown: float  # pozitif sayi, orn. 0.12 = %12
    sharpe: float
    sortino: float
    exposure: float  # pozisyonda gecen bar orani
    years: float

    def as_dict(self) -> dict:
        return asdict(self)


def bars_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 365.0
    step = pd.Series(index).diff().median().total_seconds()
    return 365.25 * 86400 / step if step > 0 else 365.0


def max_drawdown(equity: pd.Series | np.ndarray) -> float:
    eq = np.asarray(equity, dtype=float)
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.where(peak > 0, peak, 1.0)
    return float(dd.max())


def compute_metrics(trades: pd.DataFrame, equity: pd.Series) -> Metrics:
    n = len(trades)
    pnl = trades["pnl"].to_numpy(dtype=float) if n else np.array([])
    pnl_pct = trades["pnl_pct"].to_numpy(dtype=float) if n else np.array([])
    wins = pnl_pct[pnl > 0]
    losses = pnl_pct[pnl <= 0]
    gross_win = pnl[pnl > 0].sum()
    gross_loss = -pnl[pnl <= 0].sum()
    if gross_loss > 0:
        pf = gross_win / gross_loss
    else:
        pf = math.inf if gross_win > 0 else 0.0

    eq = equity.to_numpy(dtype=float)
    total_return = eq[-1] / eq[0] - 1.0 if len(eq) > 1 and eq[0] > 0 else 0.0
    bpy = bars_per_year(equity.index)
    years = len(eq) / bpy if bpy else 0.0
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 and total_return > -1 else -1.0

    rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([])
    std = rets.std(ddof=1) if len(rets) > 1 else 0.0
    sharpe = rets.mean() / std * math.sqrt(bpy) if std > 0 else 0.0
    downside = rets[rets < 0]
    dstd = math.sqrt((downside**2).mean()) if len(downside) else 0.0
    sortino = rets.mean() / dstd * math.sqrt(bpy) if dstd > 0 else 0.0
    exposure = float((rets != 0).mean()) if len(rets) else 0.0

    return Metrics(
        n_trades=n,
        win_rate=float(len(wins) / n) if n else 0.0,
        profit_factor=float(pf),
        expectancy_pct=float(pnl_pct.mean()) if n else 0.0,
        avg_win_pct=float(wins.mean()) if len(wins) else 0.0,
        avg_loss_pct=float(losses.mean()) if len(losses) else 0.0,
        total_return=float(total_return),
        cagr=float(cagr),
        max_drawdown=max_drawdown(eq),
        sharpe=float(sharpe),
        sortino=float(sortino),
        exposure=exposure,
        years=float(years),
    )
