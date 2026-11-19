"""Bar bazli, canliyla birebir ayni kurallari uygulayan backtest simulatoru.

Neden backtesting.py degil: cikis katmani (stop/TP/trailing/zaman),
risk tabanli boyut ve devre kesicilerin canli runner'la AYNI saf
fonksiyonlardan (risk/exits.py, risk/position_sizing.py,
risk/breakers.py) gecmesi gerekiyor. Ayrica vektorel sinyallerle tek
bir Python dongusu, 3 yillik 1h veride milisaniyeler surer -- walk-forward
ve hassasiyet analizi bunu gerektirir.

Dolum kurallari (canliyla ayni sira):
- Sinyal bar t KAPANISINDA uretilir, emir bar t+1 ACILISINDA dolar.
- Stop/TP bar icinde tetiklenir; ayni barda ikisi de degerse once stop.
  Bosluk (gap) varsa acilistan dolar.
- Her dolumda kayma (slippage) aleyhte uygulanir, komisyon notional uzerinden.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from tradecrm.risk import exits as ex
from tradecrm.risk.breakers import BreakerConfig, ClosedTradeLite, evaluate_entry
from tradecrm.risk.position_sizing import SizingConfig, compute_total_fraction
from tradecrm.risk.target_sizing import elapsed_fraction_of_utc_day
from tradecrm.strategy import indicators as ind
from tradecrm.strategy.base import Strategy


@dataclass
class SimConfig:
    initial_cash: float = 10_000.0
    fee_rate: float = 0.001
    slippage_bps: float = 5.0
    exits: ex.ExitConfig | None = None
    sizing: SizingConfig = field(default_factory=SizingConfig)
    breakers: BreakerConfig | None = None


@dataclass
class SimResult:
    trades: pd.DataFrame
    equity: pd.Series
    blocked: dict[str, int]
    config: SimConfig

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.config.initial_cash


TRADE_COLUMNS = [
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "qty",
    "notional",
    "fees",
    "pnl",
    "pnl_pct",
    "return_pct",
    "bars_held",
    "exit_reason",
]


@dataclass
class _Position:
    entry_time: datetime
    entry_price: float
    qty: float
    notional: float
    entry_fee: float
    balance_at_entry: float
    levels: ex.PositionLevels | None
    bars_held: int = 0


def simulate(
    df: pd.DataFrame,
    strategy: Strategy,
    cfg: SimConfig,
    trade_start: int = 0,
    signals: pd.Series | None = None,
    close_at_end: bool = True,
) -> SimResult:
    """trade_start: bu indeksten ONCEKI barlarda yeni giris yapilmaz
    (walk-forward'da gostergelerin isinmasi icin onceki veriler verilir)."""
    if getattr(strategy, "direction", "long") == "short":
        from tradecrm.backtest.short_simulator import simulate_short

        return simulate_short(df, strategy, cfg, trade_start=trade_start, signals=signals, close_at_end=close_at_end)
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

    cash = cfg.initial_cash
    pos: _Position | None = None
    pending: str | None = None  # "buy" | "sell:<reason>"
    halted = False
    day_block = None
    records: list[dict] = []
    lite: list[ClosedTradeLite] = []
    blocked: dict[str, int] = {}
    equity = np.empty(n)

    def close_position(i: int, raw_price: float, reason: str) -> None:
        nonlocal cash, pos
        assert pos is not None
        fill = raw_price * (1 - slip)
        proceeds = pos.qty * fill
        exit_fee = proceeds * cfg.fee_rate
        cash += proceeds - exit_fee
        pnl = proceeds - exit_fee - pos.notional
        pnl_pct = pnl / pos.balance_at_entry if pos.balance_at_entry else 0.0
        records.append(
            {
                "entry_time": pos.entry_time,
                "exit_time": times[i],
                "entry_price": pos.entry_price,
                "exit_price": fill,
                "qty": pos.qty,
                "notional": pos.notional,
                "fees": pos.entry_fee + exit_fee,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "return_pct": fill / pos.entry_price - 1.0,
                "bars_held": pos.bars_held,
                "exit_reason": reason,
            }
        )
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
        if pending == "buy" and pos is None:
            price = o[i] * (1 + slip)
            atr_sig = atr[i - 1] if i > 0 else np.nan
            stop = None
            if cfg.exits is not None:
                if not (atr_sig > 0):
                    pending = None
                    block("invalid_atr")
                else:
                    stop = ex.initial_stop_price(price, atr_sig, cfg.exits)
            if pending == "buy":
                frac, _ = compute_total_fraction(
                    cfg.sizing,
                    entry_price=price,
                    stop_price=stop,
                    realized_pnl_pct_today=realized_today_pct(ts),
                    elapsed_fraction_of_day=elapsed_fraction_of_utc_day(ts),
                )
                notional = cash * frac
                if notional > 0:
                    fee = notional * cfg.fee_rate
                    qty = (notional - fee) / price
                    levels = ex.initial_levels(price, atr_sig, cfg.exits) if cfg.exits else None
                    pos = _Position(ts, price, qty, notional, fee, cash, levels)
                    cash -= notional
                else:
                    block("zero_size")
        elif pending is not None and pending.startswith("sell:") and pos is not None:
            close_position(i, o[i], pending[5:])
        pending = None

        # 2) Bar ici stop/TP.
        if pos is not None and pos.levels is not None:
            decision = ex.check_bar_exit(pos.levels, o[i], h[i], lo[i])
            if decision is not None:
                pos.bars_held += 1
                close_position(i, decision.fill_price, decision.reason)

        # 3) Bar kapanisi: seviyeleri guncelle, zaman stopu, sinyal.
        if pos is not None:
            pos.bars_held += 1
            if pos.levels is not None:
                pos.levels = ex.update_on_close(pos.levels, h[i], atr[i], cfg.exits)
                if ex.time_stop_due(pos.bars_held, cfg.exits):
                    pending = "sell:time_stop"
            if pending is None and sig[i] == -1:
                pending = "sell:signal"
            # Gunluk zarar, gerceklesmemis dahil (canlidaki kill-switch ile ayni).
            if cfg.breakers is not None:
                unreal = (pos.qty * c[i] * (1 - slip) * (1 - cfg.fee_rate) - pos.notional) / pos.balance_at_entry
                if realized_today_pct(ts) + unreal <= -abs(cfg.breakers.max_daily_loss_pct):
                    day_block = ts.date()
        elif sig[i] == 1 and i >= trade_start and i < n - 1:
            if halted:
                block("halted")
            elif day_block == ts.date():
                block("day_block")  # gunluk zarar ya da gunluk kar hedefi
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
                    pending = "buy"

        equity[i] = cash + (pos.qty * c[i] if pos is not None else 0.0)

    if close_at_end and pos is not None:
        close_position(n - 1, c[-1], "end_of_data")
        equity[-1] = cash

    trades = pd.DataFrame(records, columns=TRADE_COLUMNS)
    return SimResult(trades=trades, equity=pd.Series(equity, index=df.index, name="equity"), blocked=blocked, config=cfg)


def config_from_profile(profile, initial_cash: float = 10_000.0, fee_rate: float = 0.001, slippage_bps: float = 5.0) -> SimConfig:
    return SimConfig(
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        exits=profile.exits,
        sizing=profile.sizing,
        breakers=profile.breakers,
    )
