"""Islem dizisinin yeniden orneklenmesiyle (bootstrap) sonuc dagilimi.

Tek bir backtest egrisi, olasi sonuclardan sadece biridir. Ayni islem
dagilimi farkli bir sirayla gelseydi drawdown ne olurdu? Kabul kapisi
p95 drawdown'a bakar, tek egrinin drawdown'una degil.
"""

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class MonteCarloResult:
    n_sims: int
    n_trades: int
    max_dd_p50: float
    max_dd_p95: float
    total_return_p5: float
    total_return_p50: float
    prob_loss: float

    def as_dict(self) -> dict:
        return asdict(self)


def bootstrap_trades(pnl_pct: np.ndarray, n_sims: int = 2000, seed: int = 42) -> MonteCarloResult:
    """pnl_pct: islem basina hesap getirisi (orn. 0.01 = +%1). Her
    simulasyonda ayni sayida islem, yerine koyarak cekilir ve bilesik
    equity egrisi hesaplanir."""
    pnl_pct = np.asarray(pnl_pct, dtype=float)
    n = len(pnl_pct)
    if n == 0:
        return MonteCarloResult(n_sims, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    rng = np.random.default_rng(seed)
    samples = rng.choice(pnl_pct, size=(n_sims, n), replace=True)
    curves = np.cumprod(1.0 + samples, axis=1)
    curves = np.concatenate([np.ones((n_sims, 1)), curves], axis=1)
    peaks = np.maximum.accumulate(curves, axis=1)
    max_dd = ((peaks - curves) / peaks).max(axis=1)
    total = curves[:, -1] - 1.0

    return MonteCarloResult(
        n_sims=n_sims,
        n_trades=n,
        max_dd_p50=float(np.percentile(max_dd, 50)),
        max_dd_p95=float(np.percentile(max_dd, 95)),
        total_return_p5=float(np.percentile(total, 5)),
        total_return_p50=float(np.percentile(total, 50)),
        prob_loss=float((total < 0).mean()),
    )
