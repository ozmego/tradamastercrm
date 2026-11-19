import pytest

from tradecrm.api.services.accounts import exposure_cap
from tradecrm.risk.position_sizing import compute_total_fraction
from tradecrm.strategy.profiles import PROFILES, get_profile

# Eski karsilastirma referanslari (Oneri 1-3, EMA/RSI) kendi boyut ayarlarini korur.
REFERENCE = {"exits_risk", "guarded"}
RISK_PROFILES = [n for n, p in PROFILES.items() if p.sizing.mode == "risk" and n not in REFERENCE]


@pytest.mark.parametrize("name", RISK_PROFILES)
def test_risk_profiles_cap_total_exposure_at_35pct(name):
    assert get_profile(name).sizing.max_total_fraction == pytest.approx(0.35)


def test_active_profile_cap_and_single_trade_clamp():
    sizing = get_profile("donchian_fng").sizing
    assert sizing.risk_per_trade_pct == pytest.approx(0.0075)
    # stop cok yakin (dusuk volatilite): %0,75 risk -> %75 pozisyon isterdi; %35 ile sinirlanir
    frac, _ = compute_total_fraction(sizing, entry_price=100.0, stop_price=99.0)
    assert frac == pytest.approx(0.35)
    # normal: 3 ATR = %6 uzak stop -> %12,5 pozisyon (tavanin altinda)
    frac, _ = compute_total_fraction(sizing, entry_price=100.0, stop_price=94.0)
    assert frac == pytest.approx(0.0075 * 100 / 6)


def test_exposure_cap_helper():
    assert exposure_cap("donchian_fng") == pytest.approx(0.35)
    assert exposure_cap(None) is None and exposure_cap("yok") is None
