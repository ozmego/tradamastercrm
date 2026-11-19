"""Profil = "neyin calistigi"nin tek tanimi: strateji + parametreler +
cikis katmani + pozisyon boyutu + devre kesiciler.

Backtest (simulator, walk-forward) ve canli runner AYNI profil nesnesini
kullanir; boylece iki ortamda farkli ayarlarla calisma riski ortadan kalkar.
Yeni bir sikilastirma onerisi = yeni bir profil; `backtest compare` ile
eskisiyle ayni duzenekte karsilastirilir.
"""

from dataclasses import dataclass, field, replace

from tradecrm.risk.breakers import BreakerConfig
from tradecrm.risk.exits import ExitConfig
from tradecrm.risk.position_sizing import SizingConfig
from tradecrm.strategy.base import Strategy
from tradecrm.strategy.filters import FILTERS, FilteredStrategy, build_filter
from tradecrm.strategy.registry import get_strategy


@dataclass(frozen=True)
class Profile:
    name: str
    strategy_name: str
    strategy_params: dict = field(default_factory=dict)
    exits: ExitConfig | None = None
    sizing: SizingConfig = field(default_factory=SizingConfig)
    breakers: BreakerConfig | None = None
    # Giris filtreleri: ((filtre_adi, {parametreler}), ...) -- bkz. strategy/filters.py
    filters: tuple[tuple[str, dict], ...] = ()
    description: str = ""

    def build_strategy(self, **override_params) -> Strategy:
        inner = get_strategy(self.strategy_name, **{**self.strategy_params, **override_params})
        if not self.filters:
            return inner
        return FilteredStrategy(inner, [build_filter(name, **params) for name, params in self.filters])

    @property
    def required_sources(self) -> set[str]:
        """Bu profilin filtrelerinin ihtiyac duydugu ek veri kaynaklari."""
        out = set()
        for name, _params in self.filters:
            src = FILTERS[name].source
            if src:
                out.add(src)
        return out

    def with_params(self, **params) -> "Profile":
        return replace(self, strategy_params={**self.strategy_params, **params})


PROFILES: dict[str, Profile] = {}


def register_profile(profile: Profile) -> Profile:
    PROFILES[profile.name] = profile
    return profile


def get_profile(name: str) -> Profile:
    if name not in PROFILES:
        raise KeyError(f"Bilinmeyen profil: {name!r}. Mevcut: {', '.join(sorted(PROFILES))}")
    return PROFILES[name]


# --- Referans: mevcut davranis (cikis sadece ters kesisim, sabit %75) ---------
register_profile(
    Profile(
        name="baseline",
        strategy_name="ema_rsi_crossover",
        exits=None,
        sizing=SizingConfig(mode="fixed", fixed_fraction=0.75),
        breakers=BreakerConfig(
            max_consecutive_losses=None, max_weekly_loss_pct=None, max_drawdown_pct=None
        ),
        description="Faz 8 davranisi: EMA/RSI, sadece sinyal cikisi, sabit %75, sadece gunluk zarar limiti.",
    )
)

# --- Oneri 1: cikis katmani (boyut sabit, sadece cikislarin etkisi olculur) ---
register_profile(
    Profile(
        name="exits",
        strategy_name="ema_rsi_crossover",
        exits=ExitConfig(),
        sizing=SizingConfig(mode="fixed", fixed_fraction=0.75),
        breakers=BreakerConfig(
            max_consecutive_losses=None, max_weekly_loss_pct=None, max_drawdown_pct=None
        ),
        description="baseline + ATR stop/TP/basabas/trailing.",
    )
)

# --- Oneri 1+2: cikis + risk tabanli boyut --------------------------------------
register_profile(
    Profile(
        name="exits_risk",
        strategy_name="ema_rsi_crossover",
        exits=ExitConfig(),
        sizing=SizingConfig(mode="risk", risk_per_trade_pct=0.01),
        breakers=BreakerConfig(
            max_consecutive_losses=None, max_weekly_loss_pct=None, max_drawdown_pct=None
        ),
        description="exits + islem basina %1 risk.",
    )
)

# --- Oneri 1+2+3: tam kayip kontrolu -------------------------------------------
register_profile(
    Profile(
        name="guarded",
        strategy_name="ema_rsi_crossover",
        exits=ExitConfig(),
        sizing=SizingConfig(mode="risk", risk_per_trade_pct=0.01),
        breakers=BreakerConfig(),
        description="exits_risk + devre kesiciler (art arda kayip, haftalik, drawdown).",
    )
)


# --- Oneri 4/5/7: trend takibi icin sikilastirilmis profiller (1h icin) -----------
# Trend takibinde dar stop gurultuyle tetiklenir: daha genis stop, TP yok,
# kar trailing ile korunur.
_TREND_EXITS = ExitConfig(stop_atr_mult=3.0, take_profit_atr_mult=None, breakeven_at_r=1.5, trail_atr_mult=4.0)
# Toplam acik pozisyon tavani sermayenin %35'i (2026-11-18 kullanici karari; once %75).
_RISK = SizingConfig(mode="risk", risk_per_trade_pct=0.01, max_total_fraction=0.35)
# Gunluk kar hedefi +%5: ulasilinca o gun yeni alim yok (2026-11-18 kullanici karari). Pozisyon buyutulmez.
_BREAKERS = BreakerConfig(daily_profit_target_pct=0.05)

register_profile(
    Profile(
        name="trend",
        strategy_name="ema_rsi_crossover",
        filters=(("trend", {"ema_length": 200}), ("adx", {"min_adx": 20})),
        exits=_TREND_EXITS,
        sizing=_RISK,
        breakers=_BREAKERS,
        description="Oneri 4: guarded + EMA200 trend + ADX>20 filtresi, trend cikislari.",
    )
)

register_profile(
    Profile(
        name="trend_mtf",
        strategy_name="ema_rsi_crossover",
        filters=(
            ("trend", {"ema_length": 200}),
            ("adx", {"min_adx": 20}),
            ("htf_trend", {"base_timeframe": "1h", "higher_timeframe": "4h", "ema_length": 50}),
        ),
        exits=_TREND_EXITS,
        sizing=_RISK,
        breakers=_BREAKERS,
        description="Oneri 4+5: trend + 4h EMA50 teyidi.",
    )
)

register_profile(
    Profile(
        name="donchian",
        strategy_name="donchian_breakout",
        filters=(("trend", {"ema_length": 200}),),
        exits=_TREND_EXITS,
        sizing=_RISK,
        breakers=_BREAKERS,
        description="Oneri 7: 40/20 Donchian kirilimi + EMA200 trend, trend cikislari.",
    )
)

register_profile(
    Profile(
        name="donchian_mtf",
        strategy_name="donchian_breakout",
        filters=(
            ("trend", {"ema_length": 200}),
            ("htf_trend", {"base_timeframe": "1h", "higher_timeframe": "4h", "ema_length": 50}),
        ),
        exits=_TREND_EXITS,
        sizing=_RISK,
        breakers=_BREAKERS,
        description="Oneri 5+7: donchian + 4h EMA50 teyidi.",
    )
)


# --- Oneri 11a/c/d: veri kaynagi filtreleri -- her biri TEK BASINA test edilir -----
# Taban: donchian (en iyi aday). Her profil tek bir kaynak ekler ki katkisi
# ayri olculebilsin (Oneri 6 kurali: katkisi kanitlanmayan filtre eklenmez).
_DONCHIAN_BASE = PROFILES["donchian"]


def _donchian_plus(name: str, extra: tuple, description: str) -> Profile:
    return register_profile(
        replace(_DONCHIAN_BASE, name=name, filters=_DONCHIAN_BASE.filters + extra, description=description)
    )


_donchian_plus("donchian_funding", (("funding", {"max_rate": 0.0003}),),
               "donchian + funding > %0.03/8h iken BUY yok.")
_donchian_plus("donchian_fng", (("fng", {"max_value": 80}),),
               "donchian + Fear&Greed > 80 iken BUY yok.")
_donchian_plus("donchian_macro", (("macro", {"hours_before": 4, "hours_after": 2}),),
               "donchian + FOMC'den 4s once / 2s sonra BUY yok.")


# --- Oneri 8: ortalamaya donus ---------------------------------------------------
# Dip alimi icin genis "felaket" stop'u + zaman stopu; asil cikis SMA(5) sinyali.
_MR_EXITS = ExitConfig(
    stop_atr_mult=3.0, take_profit_atr_mult=None, breakeven_at_r=None, trail_atr_mult=None, max_bars_held=12
)

register_profile(
    Profile(
        name="mean_reversion",
        strategy_name="rsi2_mean_reversion",
        filters=(("trend", {"ema_length": 200, "slope_bars": None}),),
        exits=_MR_EXITS,
        sizing=_RISK,
        breakers=_BREAKERS,
        description="Oneri 8: RSI(2)<10 dip alimi (fiyat>EMA200), SMA(5) ustunde cikis, 12 bar zaman stopu.",
    )
)

register_profile(
    Profile(
        name="mean_reversion_regime",
        strategy_name="rsi2_mean_reversion",
        filters=(
            ("trend", {"ema_length": 200, "slope_bars": None}),
            ("regime", {"allowed": ("range", "neutral")}),
        ),
        exits=_MR_EXITS,
        sizing=_RISK,
        breakers=_BREAKERS,
        description="Oneri 8: mean_reversion + sadece yatay/notr rejimde (ADX<25, asiri volatilite yok).",
    )
)


# --- Oneri 9: portfoy (alt hesaplar) ------------------------------------------------
@dataclass(frozen=True)
class Portfolio:
    """Birden cok profil, her biri sermayenin `weight` payiyla AYRI bir alt
    hesapta (paper:<profil>) calisir. Pozisyonlar ve cikis kurallari
    birbirine karismaz; cesitlendirme, sinyal birlestirmeden degil, bagimsiz
    getiri akislarinin toplanmasindan gelir."""

    name: str
    members: tuple[tuple[Profile, float], ...]
    description: str = ""

    def __post_init__(self) -> None:
        total = sum(w for _, w in self.members)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"Portfoy agirliklari toplami 1 olmali (simdi {total})")

    @property
    def required_sources(self) -> set[str]:
        return set().union(*(p.required_sources for p, _ in self.members))


def resolve(name: str) -> "Profile | Portfolio":
    """'a' -> Profile; 'a+b' -> esit agirlikli Portfolio; 'a:0.6+b:0.4' -> agirlikli."""
    if "+" not in name:
        return get_profile(name)
    parts = [p.strip() for p in name.split("+") if p.strip()]
    members = []
    for part in parts:
        pname, _, weight = part.partition(":")
        members.append((get_profile(pname), float(weight) if weight else 1.0 / len(parts)))
    return Portfolio(name=name, members=tuple(members),
                     description="portfoy: " + ", ".join(f"{p.name} %{w * 100:.0f}" for p, w in members))
