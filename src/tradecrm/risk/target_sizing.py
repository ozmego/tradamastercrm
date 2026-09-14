"""Gunluk buyume hedefine gore, islem havuzu (%75) icinde tek pozisyonun
ne kadarinin kullanilacagini oneren dinamik boyutlandirma.

Onemli: bu modul bir *oneri* uretir, guvenlik siniri degildir.
RiskManager.check_order, buradan gelen degeri MIN/MAX_POSITION_FRACTION
araligina ve %75 toplam tavanina karsi bagimsiz olarak yeniden dogrular
-- target_sizing'te olasi bir hata/asiri deger, guvenlik davranisini
gecersiz kilamaz.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class SizingResult:
    fraction_of_pool: float  # islem havuzunun (%75) 0..1 arasi orani
    pace_ratio: float | None  # >1: hedefin onunde, <1: gerisinde, None: henuz anlamli deger yok
    reason: str


def elapsed_fraction_of_utc_day(now: datetime | None = None) -> float:
    """UTC gununun ne kadarinin gectigini 0..1 arasinda dondurur."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds_since_midnight = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
    return min(1.0, max(0.0, seconds_since_midnight / 86400.0))


def compute_position_fraction(
    realized_pnl_pct_today: float,
    elapsed_fraction_of_day: float,
    daily_target_pct: float,
    min_fraction: float = 0.2,
    max_fraction: float = 1.0,
    stop_on_hit: bool = False,
) -> SizingResult:
    """Hedefin gerisinde kalindikca pozisyon buyur (max_fraction'a dogru),
    hedefte/onunde oldukca kuculur (min_fraction'a dogru). Cikti HER ZAMAN
    [min_fraction, max_fraction] (ya da stop_on_hit tetiklendiyse 0.0)
    araliginda sert clamp'lenir -- pace_ratio ne kadar kotu olursa olsun
    bu aralik asilamaz, boylece "kotu gunu buyuk pozisyonla telafi etme"
    dortusu sinirsiz buyuyemez.
    """
    if daily_target_pct <= 0:
        raise ValueError("daily_target_pct pozitif olmali")
    if not (0.0 <= min_fraction <= max_fraction <= 1.0):
        raise ValueError("0 <= min_fraction <= max_fraction <= 1 olmali")

    expected_pnl_pct_so_far = daily_target_pct * elapsed_fraction_of_day

    if stop_on_hit and realized_pnl_pct_today >= daily_target_pct:
        return SizingResult(fraction_of_pool=0.0, pace_ratio=None, reason="daily_target_reached_stop")

    pace_ratio: float | None
    if expected_pnl_pct_so_far > 1e-9:
        pace_ratio = realized_pnl_pct_today / expected_pnl_pct_so_far
    else:
        pace_ratio = None

    # shortfall_ratio > 0 => hedefin gerisinde (risk artir); < 0 => onunde (risk azalt).
    shortfall_ratio = (expected_pnl_pct_so_far - realized_pnl_pct_today) / daily_target_pct
    clamped = max(-1.0, min(1.0, shortfall_ratio))
    fraction = min_fraction + (max_fraction - min_fraction) * (clamped + 1.0) / 2.0
    # Kayan nokta sapmalarina karsi son bir sert clamp.
    fraction = max(min_fraction, min(max_fraction, fraction))

    if realized_pnl_pct_today >= daily_target_pct:
        reason = "at_or_above_target_scale_down"
    elif shortfall_ratio > 0:
        reason = "behind_target_scale_up"
    else:
        reason = "on_pace"

    return SizingResult(fraction_of_pool=fraction, pace_ratio=pace_ratio, reason=reason)
