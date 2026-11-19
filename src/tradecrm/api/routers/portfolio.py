"""Genel bakis, hesaplar, acik pozisyonlar ve pozisyon grafigi (salt okunur)."""

from datetime import UTC, datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from tradecrm.api.context import AppContext
from tradecrm.api.deps import Auth, current_user, get_ctx, get_db
from tradecrm.api.services import accounts as acc_svc
from tradecrm.db.models import Trade
from tradecrm.db.repository import AccountRepository, TradeRepository, _as_utc

router = APIRouter(tags=["portfolio"])


@router.get("/overview")
def get_overview(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                 db: Session = Depends(get_db)) -> dict:
    data = acc_svc.overview(db, ctx)
    data["processes"] = {
        "collector": ctx.collector.status(tail=0).running,
        "live": ctx.live.status(tail=0).running,
    }
    from tradecrm.api.routers.collectors import health_for
    from tradecrm.monitor.collectors import COLLECTORS, health_alerts

    statuses = {}
    for name in COLLECTORS:
        h = health_for(db, ctx, name)
        statuses[name] = h["status"]
        data["alerts"].extend(health_alerts(h))
    data["collectors"] = statuses
    return data


@router.get("/pnl")
def get_pnl(days: int = Query(30, ge=0, le=3650), account: str | None = Query(None, max_length=64, pattern=r"^[a-z]+(:[a-z0-9_-]+)?\*?$"),
            _: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
            db: Session = Depends(get_db)) -> dict:
    """Kar/zarar panosu: donemde realize edilen + acik pozisyonlarin anlik K/Z'si (days=0: tum zamanlar)."""
    from tradecrm.api.services.pnl import pnl_dashboard

    return pnl_dashboard(db, ctx.market, days=days, account=account or None)


@router.get("/accounts")
def list_accounts(_: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                  db: Session = Depends(get_db)) -> list[dict]:
    return [acc_svc.account_view(db, ctx, n) for n in acc_svc.all_account_names(db)]


@router.get("/accounts/{name}")
def get_account(name: str, _: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                db: Session = Depends(get_db)) -> dict:
    if name not in acc_svc.all_account_names(db):
        raise HTTPException(status_code=404, detail="hesap bulunamadi")
    return acc_svc.account_view(db, ctx, name)


@router.get("/accounts/{name}/equity")
def get_equity(name: str, days: int = Query(30, ge=0, le=3650), _: Auth = Depends(current_user),
               db: Session = Depends(get_db)) -> list[dict]:
    return acc_svc.equity_series(db, name, days)


def _position_row(t: Trade, price: float | None) -> dict:
    cost = t.entry_price * t.qty + (t.entry_fees or 0.0)
    mv = price * t.qty if price else None
    return {
        "id": t.id, "account": t.broker, "symbol": t.symbol, "strategy": t.strategy_name,
        "entry_time": _as_utc(t.entry_time).isoformat(), "entry_price": t.entry_price, "qty": t.qty,
        "cost": cost, "price": price, "market_value": mv,
        "unrealized_pnl": (mv - cost) if mv is not None else None,
        "unrealized_pct": (mv / cost - 1.0) if mv is not None and cost else None,
        "stop": t.stop_price, "take_profit": t.take_profit_price, "initial_stop": t.initial_stop_price,
        "highest": t.highest_price,
        "stop_distance_pct": (t.stop_price / price - 1.0) if price and t.stop_price else None,
    }


@router.get("/positions")
def list_positions(account: str | None = None, _: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                   db: Session = Depends(get_db)) -> list[dict]:
    rows = []
    for t in TradeRepository(db).get_open_trades(broker=account):
        try:
            price = float(ctx.market.fetch_latest_price(t.symbol))
        except Exception:  # noqa: BLE001 -- fiyat yoksa satir yine gosterilir
            price = None
        rows.append(_position_row(t, price))
    return rows


@router.get("/trades/{trade_id}/chart")
def trade_chart(trade_id: int, timeframe: str | None = None, limit: int = Query(200, ge=20, le=1000),
                _: Auth = Depends(current_user), ctx: AppContext = Depends(get_ctx),
                db: Session = Depends(get_db)) -> dict:
    from tradecrm.backtest.service import InvalidParamsError, validate_timeframe

    trade = db.get(Trade, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="islem bulunamadi")
    acc = AccountRepository(db).get(trade.broker)
    tf = timeframe or (acc.timeframe if acc and acc.timeframe else "1h")
    try:
        validate_timeframe(tf)
    except InvalidParamsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        df = ctx.market.fetch_latest_ohlcv(trade.symbol, timeframe=tf, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"mum verisi alinamadi: {type(exc).__name__}") from None
    candles = [] if df is None else [
        {"time": int(pd.Timestamp(ts).timestamp()), "open": float(r.Open), "high": float(r.High),
         "low": float(r.Low), "close": float(r.Close), "volume": float(r.Volume)}
        for ts, r in df.iterrows()
    ]
    return {
        "symbol": trade.symbol, "timeframe": tf, "candles": candles,
        "entry": {"time": int(_as_utc(trade.entry_time).timestamp()), "price": trade.entry_price},
        "exit": ({"time": int(_as_utc(trade.exit_time).timestamp()), "price": trade.exit_price}
                 if trade.exit_time else None),
        "levels": {"stop": trade.stop_price, "take_profit": trade.take_profit_price,
                   "initial_stop": trade.initial_stop_price},
        "generated_at": datetime.now(UTC).isoformat(),
    }
