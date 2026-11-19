"""Hesap gorunumu: kayit + risk bayraklari + anlik hesap durumu + drift.

Is mantigi kopyalanmaz: bayraklar RiskStateStore'dan, nakit/equity
PaperBrokerAdapter.get_snapshot()'tan, drift monitor/drift.py'den gelir.
Bu modul HICBIR durumu degistirmez (drift guard'in run_once'i degil,
sadece check_drift cagrilir).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.db.repository import (
    AccountRepository,
    EquityRepository,
    OrderRepository,
    _as_utc,
)
from tradecrm.monitor.drift import DriftGuard, check_drift, load_baseline
from tradecrm.risk.state import RiskStateStore


def is_live_account(name: str) -> bool:
    return not name.startswith("paper")


def _sub_account(name: str) -> str | None:
    return name.split(":", 1)[1] if ":" in name else None


def snapshot_for(db: Session, ctx: AppContext, name: str, initial_capital: float):
    from tradecrm.broker.paper_adapter import PaperBrokerAdapter

    broker = PaperBrokerAdapter(db, ctx.market, initial_capital=initial_capital, account=_sub_account(name))
    return broker.get_snapshot()


def _finite(x):
    return x if x is not None and math.isfinite(x) else None


def account_view(db: Session, ctx: AppContext, name: str, sparkline_days: int = 30) -> dict:
    acc = AccountRepository(db).get(name)
    live = is_live_account(name)
    state = RiskStateStore(db, broker=name)

    trading_enabled = state.is_trading_enabled()
    kill = state.is_kill_switch_tripped()
    first_confirmed = state.is_first_order_confirmed() if live else True
    env_ok = ctx.live_trading_allowed_by_env if live else True
    blockers = []
    if not trading_enabled:
        blockers.append("DB alim izni kapali")
    if kill:
        blockers.append("kill-switch")
    if live and not env_ok:
        blockers.append(".env ana salteri kapali")
    if live and not first_confirmed:
        blockers.append("ilk emir onayi yok")

    snapshot = None
    snapshot_error = None
    if acc is not None and not live:
        try:
            snap = snapshot_for(db, ctx, name, acc.initial_capital)
            snapshot = {
                "cash": snap.cash, "allocated": snap.allocated, "market_value": snap.market_value,
                "equity": snap.equity, "realized_equity": snap.realized_equity,
                "unrealized_pnl": snap.unrealized_pnl, "exposure": _finite(snap.exposure_fraction),
                "positions": len(snap.positions),
                "return_since_start": (snap.equity / acc.initial_capital - 1.0) if acc.initial_capital else None,
                "exposure_cap": exposure_cap(acc.profile),
            }
        except Exception as exc:  # noqa: BLE001 -- fiyat alinamasa da sayfa acilmali
            snapshot_error = f"{type(exc).__name__}: {exc}"
    elif acc is None:
        snapshot_error = "hesap kaydi yok (live run ile baslatilinca olusur)"
    else:
        # Canli hesap: panel borsaya baglanmaz; runner'in her tick'te yazdigi son kayit gosterilir.
        last = EquityRepository(db).latest(name)
        if last is None:
            snapshot_error = "henuz kayit yok (canli runner ilk tick'te yazar)"
        else:
            realized_eq = last.cash + last.allocated
            snapshot = {
                "cash": last.cash, "allocated": last.allocated, "market_value": last.market_value,
                "equity": last.equity, "realized_equity": realized_eq,
                "unrealized_pnl": last.market_value - last.allocated,
                "exposure": _finite(last.market_value / last.equity) if last.equity else None,
                "positions": last.open_positions,
                "return_since_start": (last.equity / acc.initial_capital - 1.0) if acc.initial_capital else None,
                "exposure_cap": exposure_cap(acc.profile),
                "as_of": _as_utc(last.ts).isoformat(),
            }

    today = datetime.now(UTC).date()
    realized_today = state.get_realized_pnl_pct(today)
    unrealized_pct = None
    if snapshot and snapshot["realized_equity"] > 0:
        unrealized_pct = snapshot["unrealized_pnl"] / snapshot["realized_equity"]

    target = daily_profit_target(acc.profile if acc else None)
    today_total = realized_today + (unrealized_pct or 0.0)
    if target is not None and today_total >= target:
        blockers.append(f"gunluk hedef (+{target:.0%}) dolu -- bugun yeni alim yok")

    drift = None
    if acc is not None and acc.profile and acc.timeframe:
        baseline = load_baseline(acc.profile, acc.timeframe, ctx.baseline_dir)
        guard = DriftGuard(state, baseline, notifier=None, audit_logger=None)
        res = check_drift(guard.live_returns(), baseline)
        drift = {"status": res.status, "n": res.n, "live_mean": res.live_mean, "lower_bound": res.lower_bound,
                 "baseline_mean": res.baseline_mean, "text": res.describe()}

    since = datetime.now(UTC) - timedelta(days=sparkline_days)
    series = EquityRepository(db).series(name, since)
    step = max(1, len(series) // 60)
    sparkline = [{"t": _as_utc(r.ts).isoformat(), "v": r.equity} for r in series[::step]]

    return {
        "name": name,
        "mode": "live" if live else "paper",
        "registered": acc is not None,
        "profile": acc.profile if acc else None,
        "timeframe": acc.timeframe if acc else None,
        "initial_capital": acc.initial_capital if acc else None,
        "created_at": _as_utc(acc.created_at).isoformat() if acc else None,
        "flags": {
            "trading_enabled": trading_enabled,
            "kill_switch": kill,
            "kill_switch_reason": state.kill_switch_reason,
            "env_live_allowed": env_ok if live else None,
            "first_order_confirmed": first_confirmed if live else None,
            "buy_allowed": not blockers,
            "blockers": blockers,
        },
        "snapshot": snapshot,
        "snapshot_error": snapshot_error,
        "pnl_today": {"realized_pct": realized_today, "unrealized_pct": unrealized_pct,
                      "total_pct": today_total, "target_pct": target},
        "unresolved_orders": len(OrderRepository(db).unresolved(name)),
        "drift": drift,
        "sparkline": sparkline,
    }


def exposure_cap(profile_name: str | None) -> float | None:
    """Hesabin profilindeki toplam pozisyon tavani (SizingConfig.max_total_fraction)."""
    if not profile_name:
        return None
    try:
        from tradecrm.strategy.profiles import get_profile

        return get_profile(profile_name).sizing.max_total_fraction
    except KeyError:
        return None


def daily_profit_target(profile_name: str | None) -> float | None:
    """Profilin gunluk kar hedefi (BreakerConfig.daily_profit_target_pct); yoksa None."""
    if not profile_name:
        return None
    try:
        from tradecrm.strategy.profiles import get_profile

        b = get_profile(profile_name).breakers
    except KeyError:
        return None
    return b.daily_profit_target_pct if b is not None else None


def all_account_names(db: Session) -> list[str]:
    return AccountRepository(db).known_names()


def overview(db: Session, ctx: AppContext) -> dict:
    accounts = [account_view(db, ctx, n) for n in all_account_names(db)]
    snaps = [a["snapshot"] for a in accounts if a["snapshot"]]
    totals = {
        "equity": sum(s["equity"] for s in snaps),
        "cash": sum(s["cash"] for s in snaps),
        "market_value": sum(s["market_value"] for s in snaps),
        "unrealized_pnl": sum(s["unrealized_pnl"] for s in snaps),
        "positions": sum(s["positions"] for s in snaps),
        "initial_capital": sum(a["initial_capital"] or 0 for a in accounts if a["snapshot"]),
    }
    totals["exposure"] = totals["market_value"] / totals["equity"] if totals["equity"] > 0 else None
    capped = [s for s in snaps if s.get("exposure_cap") is not None and s["equity"] > 0]
    # Birden cok hesap: equity agirlikli tavan
    totals["exposure_cap"] = (sum(s["exposure_cap"] * s["equity"] for s in capped) / sum(s["equity"] for s in capped)
                              if capped else None)
    totals["return_since_start"] = (
        totals["equity"] / totals["initial_capital"] - 1.0 if totals["initial_capital"] else None
    )

    alerts = []
    for a in accounts:
        f = a["flags"]
        if f["kill_switch"]:
            alerts.append({"level": "critical", "account": a["name"], "text": f"Kill-switch: {f['kill_switch_reason']}"})
        if a["unresolved_orders"]:
            alerts.append({"level": "critical", "account": a["name"],
                           "text": f"{a['unresolved_orders']} kesinlesmemis emir -- uzlastirilana kadar alim yok"})
        if a["drift"] and a["drift"]["status"] == "degraded":
            alerts.append({"level": "warning", "account": a["name"], "text": f"Performans bozulmasi: {a['drift']['text']}"})
        if a["snapshot_error"] and a["registered"] and a["mode"] == "paper":
            alerts.append({"level": "warning", "account": a["name"], "text": f"Hesap durumu alinamadi: {a['snapshot_error']}"})

    return {"totals": totals, "accounts": accounts, "alerts": alerts, "generated_at": datetime.now(UTC).isoformat()}


def equity_series(db: Session, name: str, days: int) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days) if days > 0 else None
    rows = EquityRepository(db).series(name, since)
    return [{"t": _as_utc(r.ts).isoformat(), "equity": r.equity, "cash": r.cash, "market_value": r.market_value}
            for r in rows]
