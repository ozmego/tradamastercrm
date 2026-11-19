"""Strateji bagimsiz cikis katmani: ATR stop, take-profit, basabas,
trailing stop, zaman stopu.

Saf fonksiyonlar -- hem backtest simulatoru hem LiveRunner AYNI
fonksiyonlari cagirir (canli/backtest paritesi). Stop seviyesi hicbir
zaman asagi cekilmez.

Bar akisi (her iki ortamda da):
  1. Pozisyon acikken her kapanmis bar icin once check_bar_exit (bar
     icinde stop/TP'ye degildi mi?),
  2. cikis yoksa bar kapanisinda update_on_close (basabas/trailing),
  3. time_stop_due ise bir sonraki barin acilisinda cikis.
"""

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ExitConfig:
    atr_period: int = 14
    stop_atr_mult: float = 2.0
    take_profit_atr_mult: float | None = 4.0  # None: TP yok (trend'in kosmasina izin ver)
    breakeven_at_r: float | None = 1.0  # fiyat entry + 1R'ye ulasinca stop -> entry
    trail_atr_mult: float | None = 3.0  # basabas tetiklendikten sonra highest - k*ATR
    max_bars_held: int | None = None  # zaman stopu

    def __post_init__(self) -> None:
        if self.stop_atr_mult <= 0:
            raise ValueError("stop_atr_mult pozitif olmali")


@dataclass(frozen=True)
class PositionLevels:
    entry_price: float
    initial_stop: float
    stop: float
    take_profit: float | None
    highest: float

    @property
    def risk_per_unit(self) -> float:
        return self.entry_price - self.initial_stop


@dataclass(frozen=True)
class ExitDecision:
    reason: str  # "stop_loss" | "breakeven_stop" | "trailing_stop" | "take_profit"
    fill_price: float


def initial_stop_price(entry_price: float, atr_value: float, cfg: ExitConfig) -> float:
    return entry_price - cfg.stop_atr_mult * atr_value


def initial_levels(entry_price: float, atr_value: float, cfg: ExitConfig) -> PositionLevels:
    if not atr_value or math.isnan(atr_value) or atr_value <= 0:  # NaN/0 korumasi
        raise ValueError(f"Gecersiz ATR: {atr_value}")
    stop = initial_stop_price(entry_price, atr_value, cfg)
    tp = entry_price + cfg.take_profit_atr_mult * atr_value if cfg.take_profit_atr_mult else None
    return PositionLevels(entry_price=entry_price, initial_stop=stop, stop=stop, take_profit=tp, highest=entry_price)


def _stop_reason(levels: PositionLevels) -> str:
    if levels.stop <= levels.initial_stop:
        return "stop_loss"
    if levels.stop <= levels.entry_price + 1e-12:
        return "breakeven_stop"
    return "trailing_stop"


def check_bar_exit(levels: PositionLevels, bar_open: float, bar_high: float, bar_low: float) -> ExitDecision | None:
    """Bar icinde stop/TP tetiklendi mi? Ayni barda ikisi de degdiyse
    muhafazakar varsayim: once stop. Bosluk (gap) varsa acilis fiyatindan
    dolar."""
    if bar_low <= levels.stop:
        fill = min(bar_open, levels.stop)
        return ExitDecision(_stop_reason(levels), fill)
    if levels.take_profit is not None and bar_high >= levels.take_profit:
        fill = max(bar_open, levels.take_profit)
        return ExitDecision("take_profit", fill)
    return None


def update_on_close(levels: PositionLevels, bar_high: float, atr_value: float, cfg: ExitConfig) -> PositionLevels:
    highest = max(levels.highest, bar_high)
    stop = levels.stop
    r = levels.risk_per_unit

    activated = cfg.breakeven_at_r is not None and r > 0 and highest >= levels.entry_price + cfg.breakeven_at_r * r
    if activated:
        stop = max(stop, levels.entry_price)
        if cfg.trail_atr_mult is not None and not math.isnan(atr_value) and atr_value > 0:
            stop = max(stop, highest - cfg.trail_atr_mult * atr_value)

    return replace(levels, highest=highest, stop=stop)


def time_stop_due(bars_held: int, cfg: ExitConfig) -> bool:
    return cfg.max_bars_held is not None and bars_held >= cfg.max_bars_held


# ---------------------------------------------------------------- KISA (short)
# Ayna kurallar: stop girisin USTUNDE, TP altinda; basabas/trailing en DUSUK
# fiyata gore; stop hicbir zaman yukari cekilmez. PositionLevels.highest alani
# kisa pozisyonda "gorulen en dusuk fiyat"i tutar.


def short_initial_stop_price(entry_price: float, atr_value: float, cfg: ExitConfig) -> float:
    return entry_price + cfg.stop_atr_mult * atr_value


def short_initial_levels(entry_price: float, atr_value: float, cfg: ExitConfig) -> PositionLevels:
    if not atr_value or math.isnan(atr_value) or atr_value <= 0:
        raise ValueError(f"Gecersiz ATR: {atr_value}")
    stop = short_initial_stop_price(entry_price, atr_value, cfg)
    tp = entry_price - cfg.take_profit_atr_mult * atr_value if cfg.take_profit_atr_mult else None
    return PositionLevels(entry_price=entry_price, initial_stop=stop, stop=stop, take_profit=tp, highest=entry_price)


def _short_stop_reason(levels: PositionLevels) -> str:
    if levels.stop >= levels.initial_stop:
        return "stop_loss"
    if levels.stop >= levels.entry_price - 1e-12:
        return "breakeven_stop"
    return "trailing_stop"


def short_check_bar_exit(levels: PositionLevels, bar_open: float, bar_high: float,
                         bar_low: float) -> ExitDecision | None:
    """Kisa pozisyon: fiyat stop'a YUKARI degerse cikis (bosluklu acilista acilistan, yani daha kotu)."""
    if bar_high >= levels.stop:
        return ExitDecision(_short_stop_reason(levels), max(bar_open, levels.stop))
    if levels.take_profit is not None and bar_low <= levels.take_profit:
        return ExitDecision("take_profit", min(bar_open, levels.take_profit))
    return None


def short_update_on_close(levels: PositionLevels, bar_low: float, atr_value: float, cfg: ExitConfig) -> PositionLevels:
    lowest = min(levels.highest, bar_low)
    stop = levels.stop
    r = levels.initial_stop - levels.entry_price
    activated = cfg.breakeven_at_r is not None and r > 0 and lowest <= levels.entry_price - cfg.breakeven_at_r * r
    if activated:
        stop = min(stop, levels.entry_price)
        if cfg.trail_atr_mult is not None and not math.isnan(atr_value) and atr_value > 0:
            stop = min(stop, lowest + cfg.trail_atr_mult * atr_value)
    return replace(levels, highest=lowest, stop=stop)
