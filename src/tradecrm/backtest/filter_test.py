"""Oneri 6 kabul kriteri: bir giris filtresinin katkisi istatistiksel olarak
anlamli mi?

Yontem (katki analizi): taban profilin walk-forward OOS islemlerinin her biri
icin, sinyal barinda filtrenin girise izin verip vermeyecegine bakilir.
Islemler "kalan" ve "elenen" diye ikiye ayrilir; sifir hipotezi "filtre
islemleri rastgele eliyor" olur. Elenenlerin ortalama fiyat getirisi,
ayni sayida islemin rastgele secildigi permutasyonlarla karsilastirilir
(tek yonlu: elenenler daha KOTU mu?).

Not: filtreli profilde sermaye/pozisyon cakismasi degistigi icin islem
listesi tam olarak "kalanlar" olmaz; bu yuzden komut ayrica filtreli
profilin tam walk-forward sonucunu da yan yana gosterir. Karar katki
testine gore verilir; birden cok filtre denendiginde Bonferroni
duzeltmesi uygulanir.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradecrm.strategy.filters import EntryFilter


@dataclass
class FilterAttribution:
    filter_name: str
    n_kept: int
    n_removed: int
    kept_mean: float
    removed_mean: float
    removed_win_rate: float
    p_value: float  # P(rastgele eleme, gozlenen kadar kotu islemleri eler)

    @property
    def removed_frac(self) -> float:
        total = self.n_kept + self.n_removed
        return self.n_removed / total if total else 0.0


def signal_positions(df: pd.DataFrame, entry_times: pd.Series) -> np.ndarray:
    """Giris, sinyal barindan SONRAKI barin acilisinda yapilir: sinyal = giris - 1."""
    pos = df.index.get_indexer(pd.DatetimeIndex(entry_times))
    if (pos < 1).any():
        raise ValueError("Islem giris zamani veride yok ya da ilk bar")
    return pos - 1


def trade_allowed(df: pd.DataFrame, trades: pd.DataFrame, flt: EntryFilter) -> np.ndarray:
    if not len(trades):
        return np.zeros(0, dtype=bool)
    return np.asarray(flt.allows_trade(df, signal_positions(df, trades["entry_time"])), dtype=bool)


def permutation_p_value(returns: np.ndarray, removed: np.ndarray, n_perm: int = 20_000, seed: int = 0) -> float:
    """Tek yonlu: rastgele secilen ayni sayidaki islemin ortalamasinin,
    gercekten elenenlerin ortalamasina esit ya da daha dusuk olma olasiligi."""
    returns = np.asarray(returns, dtype=float)
    k = int(removed.sum())
    if k == 0 or k == len(returns):
        return 1.0
    observed = returns[removed].mean()
    rng = np.random.default_rng(seed)
    idx = np.argsort(rng.random((n_perm, len(returns))), axis=1)[:, :k]
    perm_means = returns[idx].mean(axis=1)
    return float((np.sum(perm_means <= observed + 1e-15) + 1) / (n_perm + 1))


def attribute_filter(
    datasets: dict[str, pd.DataFrame],
    trades_by_symbol: dict[str, pd.DataFrame],
    flt: EntryFilter,
    n_perm: int = 20_000,
    seed: int = 0,
) -> FilterAttribution:
    rets, removed = [], []
    for symbol, trades in trades_by_symbol.items():
        if not len(trades):
            continue
        allowed = trade_allowed(datasets[symbol], trades, flt)
        rets.append(trades["return_pct"].to_numpy(dtype=float))
        removed.append(~allowed)
    r = np.concatenate(rets) if rets else np.zeros(0)
    rm = np.concatenate(removed) if removed else np.zeros(0, dtype=bool)
    kept = r[~rm]
    gone = r[rm]
    return FilterAttribution(
        filter_name=flt.name,
        n_kept=len(kept),
        n_removed=len(gone),
        kept_mean=float(kept.mean()) if len(kept) else float("nan"),
        removed_mean=float(gone.mean()) if len(gone) else float("nan"),
        removed_win_rate=float((gone > 0).mean()) if len(gone) else float("nan"),
        p_value=permutation_p_value(r, rm, n_perm=n_perm, seed=seed),
    )
