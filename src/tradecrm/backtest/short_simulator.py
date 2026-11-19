"""Kisa yonlu (short) backtest simulatoru -- ARASTIRMA amacli.

Long simulatorle (simulator.py) AYNI kurallar, ayna yonde:
- Sinyal bar t kapanisinda (kod 1 = short ac, -1 = kapat), dolum bar t+1 acilisinda.
- Stop girisin USTUNDE (giris + k*ATR); bar icinde fiyat stop'a YUKARI degerse
  cikis, bosluklu acilista acilistan (daha kotu) dolar. Basabas/trailing en
  dusuk fiyata gore (risk/exits.py short_* fonksiyonlari).
- Boyut risk tabanli: stop'a kadar kayip = equity * risk_pct; toplam tavan
  max_total_fraction. Kaldirac yok (1x): teminat = pozisyon degeri.
- Devre kesiciler ve gunluk zarar/hedef long ile ayni (breakers.evaluate_entry).
- Kayma her dolumda aleyhte (short giris daha dusuk, kapanis daha yuksek fiyat).
- FONLAMA: vadeli islemde short taraf, fonlama orani pozitifken ALIR, negatifken
  ODER. Veride `funding_rate` (8 saatlik oran, son bilinen) varsa her bar icin
  pozisyon degeri * oran * (bar_saat / 8) eklenir; yoksa 0 varsayilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from tradecrm.backtest.simulator import TRADE_COLUMNS, SimConfig, SimResult
from tradecrm.risk import exits as ex
from tradecrm.risk.breakers import ClosedTradeLite, evaluate_entry
from tradecrm.strategy import indicators as ind


@dataclass
class _Short:
    entry_time: datetime
    entry_price: float
    qty: float
    notional: float  # teminat (1x: pozisyon degeri)
    entry_fee: float
    balance_at_entry: float
    levels: ex.PositionLevels | None
    funding: float = 0.0
    bars_held: int = 0


def _risk_fraction(risk_pct: float, entry: float, stop: float, max_fraction: float) -> float:
    distance = stop - entry
    if distance <= 0 or entry <= 0:
        return 0.0
    return max(0.0, min(max_fraction, risk_pct * entry / distance))


def simulate_short(df: pd.DataFrame, strategy, cfg: SimConfig, trade_start: int = 0,
                   signals: pd.Series | None = None, close_at_end: bool = True) -> SimResult:
    n = len(df)
    if signals is None:
        signals = strategy.generate_signals(df)
    sig = signals.to_numpy()
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    lo = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    times = list(df.index.to_pydatetime())
    atr = ind.atr(df, cfg.exits.atr_period).to_numpy(dtype=float) if cfg.exits else np.full(n, np.nan)
    slip = cfg.slippage_bps / 10_000.0
    if "funding_rate" in df.columns and n > 1:
        bar_hours = (df.index[1] - df.index[0]).total_seconds() / 3600
        funding = np.nan_to_num(df["funding_rate"].to_numpy(dtype=float)) * (bar_hours / 8.0)
    else:
        funding = np.zeros(n)

    cash = cfg.initial_cash
    pos: _Short | None = None
    pending: str | None = None  # "open" | "close:<neden>"
    halted = False
    day_block = None
    records: list[dict] = []
    lite: list[ClosedTradeLite] = []
    blocked: dict[str, int] = {}
    equity = np.empty(n)

    def value(p: _Short, price: float) -> float:
        """Teminat + gerceklesmemis K/Z + birikmis fonlama."""
        return p.qty * p.entry_price + (p.entry_price - price) * p.qty + p.funding

    def close_position(i: int, raw_price: float, reason: str) -> None:
        nonlocal cash, pos
        assert pos is not None
        fill = raw_price * (1 + slip)
        exit_fee = pos.qty * fill * cfg.fee_rate
        cash += value(pos, fill) - exit_fee
        pnl = (pos.entry_price - fill) * pos.qty + pos.funding - pos.entry_fee - exit_fee
        pnl_pct = pnl / pos.balance_at_entry if pos.balance_at_entry else 0.0
        records.append({
            "entry_time": pos.entry_time, "exit_time": times[i], "entry_price": pos.entry_price,
            "exit_price": fill, "qty": pos.qty, "notional": pos.notional,
            "fees": pos.entry_fee + exit_fee - pos.funding,  # alinan fonlama maliyeti azaltir
            "pnl": pnl, "pnl_pct": pnl_pct,
            "return_pct": pos.entry_price / fill - 1.0,  # kisa yonde fiyat getirisi
            "bars_held": pos.bars_held, "exit_reason": reason,
        })
        lite.append(ClosedTradeLite(exit_time=times[i], pnl=pnl, pnl_pct=pnl_pct))
        pos = None

    def realized_today_pct(ts: datetime) -> float:
        d = ts.date()
        return sum(t.pnl_pct for t in lite if t.exit_time.date() == d)

    def block(reason: str) -> None:
        key = reason.split(" ")[0]
        blocked[key] = blocked.get(key, 0) + 1

    for i in range(n):
        ts = times[i]

        # 1) Bekleyen emirler bu barin acilisinda dolar.
        if pending == "open" and pos is None:
            price = o[i] * (1 - slip)
            atr_sig = atr[i - 1] if i > 0 else np.nan
            if cfg.exits is None or not (atr_sig > 0):
                block("invalid_atr")
            else:
                stop = ex.short_initial_stop_price(price, atr_sig, cfg.exits)
                frac = _risk_fraction(cfg.sizing.risk_per_trade_pct, price, stop, cfg.sizing.max_total_fraction)
                notional = cash * frac
                if notional > 0:
                    fee = notional * cfg.fee_rate
                    qty = (notional - fee) / price
                    levels = ex.short_initial_levels(price, atr_sig, cfg.exits)
                    pos = _Short(ts, price, qty, notional, fee, cash, levels)
                    cash -= notional
                else:
                    block("zero_size")
        elif pending is not None and pending.startswith("close:") and pos is not None:
            close_position(i, o[i], pending[6:])
        pending = None

        # 2) Bar ici stop/TP.
        if pos is not None and pos.levels is not None:
            decision = ex.short_check_bar_exit(pos.levels, o[i], h[i], lo[i])
            if decision is not None:
                pos.bars_held += 1
                close_position(i, decision.fill_price, decision.reason)

        # 3) Bar kapanisi: fonlama, seviyeler, zaman stopu, sinyal, gunluk zarar.
        if pos is not None:
            pos.bars_held += 1
            pos.funding += pos.qty * c[i] * funding[i]
            if pos.levels is not None:
                pos.levels = ex.short_update_on_close(pos.levels, lo[i], atr[i], cfg.exits)
                if ex.time_stop_due(pos.bars_held, cfg.exits):
                    pending = "close:time_stop"
            if pending is None and sig[i] == -1:
                pending = "close:signal"
            if cfg.breakers is not None:
                unreal = ((pos.entry_price - c[i] * (1 + slip)) * pos.qty + pos.funding) / pos.balance_at_entry
                if realized_today_pct(ts) + unreal <= -abs(cfg.breakers.max_daily_loss_pct):
                    day_block = ts.date()
        elif sig[i] == 1 and i >= trade_start and i < n - 1:
            if halted:
                block("halted")
            elif day_block == ts.date():
                block("day_block")
            else:
                ok = True
                if cfg.breakers is not None:
                    res = evaluate_entry(lite, ts, cfg.initial_cash, cfg.breakers)
                    if not res.allowed:
                        ok = False
                        block(res.reason)
                        if res.scope == "halt":
                            halted = True
                        elif res.scope == "day":
                            day_block = ts.date()
                if ok:
                    pending = "open"

        equity[i] = cash + (value(pos, c[i]) if pos is not None else 0.0)

    if close_at_end and pos is not None:
        close_position(n - 1, c[-1], "end_of_data")
        equity[-1] = cash

    trades = pd.DataFrame(records, columns=TRADE_COLUMNS)
    return SimResult(trades=trades, equity=pd.Series(equity, index=df.index, name="equity"), blocked=blocked, config=cfg)
