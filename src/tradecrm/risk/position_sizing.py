"""Pozisyon boyutu: TOPLAM sermayenin ne kadarinin bir pozisyona
konacagi (0..max_total_fraction).

Modlar:
- "risk" (varsayilan): her islemde sermayenin sabit bir orani riske
  edilir. Stop'a kadar kayip = equity * risk_pct. Volatilite artinca
  (stop uzaklasinca) pozisyon otomatik kuculur.
- "fixed": her islemde sabit oran (stop yoksa da calisir).
- "daily_target": eski target_sizing davranisi (geride kalinca buyur).
  Martingale benzeri oldugu icin onerilmez; sadece karsilastirma icin.
"""

from dataclasses import dataclass

from tradecrm.risk.target_sizing import compute_position_fraction


@dataclass(frozen=True)
class SizingConfig:
    mode: str = "risk"  # "risk" | "fixed" | "daily_target"
    risk_per_trade_pct: float = 0.01
    fixed_fraction: float = 0.75
    max_total_fraction: float = 0.75  # %75 toplam sermaye tavani
    # daily_target modu
    daily_target_pct: float = 0.0075
    min_pool_fraction: float = 0.2
    max_pool_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.mode not in ("risk", "fixed", "daily_target"):
            raise ValueError(f"Bilinmeyen sizing modu: {self.mode}")
        if not (0 < self.max_total_fraction <= 1):
            raise ValueError("max_total_fraction (0, 1] olmali")


def risk_based_fraction(risk_pct: float, entry_price: float, stop_price: float, max_fraction: float) -> float:
    """qty = equity*risk_pct / (entry-stop)  =>  notional/equity = risk_pct*entry/(entry-stop)."""
    distance = entry_price - stop_price
    if distance <= 0 or entry_price <= 0:
        return 0.0
    return max(0.0, min(max_fraction, risk_pct * entry_price / distance))


def compute_total_fraction(
    cfg: SizingConfig,
    entry_price: float,
    stop_price: float | None,
    realized_pnl_pct_today: float = 0.0,
    elapsed_fraction_of_day: float = 0.0,
) -> tuple[float, str]:
    """Toplam sermayenin orani ve bir aciklama dondurur."""
    if cfg.mode == "risk":
        if stop_price is None:
            raise ValueError("risk modu bir stop fiyati gerektirir (ExitConfig tanimlayin)")
        frac = risk_based_fraction(cfg.risk_per_trade_pct, entry_price, stop_price, cfg.max_total_fraction)
        return frac, f"risk_{cfg.risk_per_trade_pct:.2%}"
    if cfg.mode == "fixed":
        return min(cfg.fixed_fraction, cfg.max_total_fraction), "fixed"

    sizing = compute_position_fraction(
        realized_pnl_pct_today=realized_pnl_pct_today,
        elapsed_fraction_of_day=elapsed_fraction_of_day,
        daily_target_pct=cfg.daily_target_pct,
        min_fraction=cfg.min_pool_fraction,
        max_fraction=cfg.max_pool_fraction,
    )
    return sizing.fraction_of_pool * cfg.max_total_fraction, sizing.reason
