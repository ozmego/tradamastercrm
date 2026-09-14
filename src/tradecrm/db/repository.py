from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlmodel import Session, select

from tradecrm.db.models import RiskEvent, Trade

def _as_utc(dt: datetime) -> datetime:
    """SQLite/SQLAlchemy datetime'lari round-trip'te tzinfo'yu kaybeder
    (naive olarak geri doner). Sistemdeki her datetime UTC olarak yazildigi
    icin, naive gelen degerler dogrudan UTC kabul edilir -- .astimezone()
    naive bir datetime'i yanlislikla YEREL saat varsayip kaydirir."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_ACTIVE_FLAG_EVENTS = {
    "trading_enabled": ("TRADING_ENABLED", "TRADING_DISABLED"),
    "kill_switch": ("KILL_SWITCH_TRIP", "KILL_SWITCH_RESET"),
}


class TradeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def open_trade(
        self,
        symbol: str,
        strategy_name: str,
        broker: str,
        entry_time: datetime,
        entry_price: float,
        qty: float,
        account_balance_at_entry: float,
        signal_reason: str | None = None,
    ) -> Trade:
        trade = Trade(
            symbol=symbol,
            strategy_name=strategy_name,
            broker=broker,
            entry_time=entry_time,
            entry_price=entry_price,
            qty=qty,
            account_balance_at_entry=account_balance_at_entry,
            signal_reason=signal_reason,
            status="OPEN",
        )
        self.session.add(trade)
        self.session.commit()
        self.session.refresh(trade)
        return trade

    def close_trade(
        self,
        trade_id: int,
        exit_time: datetime,
        exit_price: float,
        fees: float = 0.0,
    ) -> Trade:
        trade = self.session.get(Trade, trade_id)
        if trade is None:
            raise KeyError(f"Trade bulunamadi: {trade_id}")
        if trade.status == "CLOSED":
            raise ValueError(f"Trade zaten kapali: {trade_id}")

        gross_pnl = (exit_price - trade.entry_price) * trade.qty
        realized_pnl = gross_pnl - fees

        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.fees = fees
        trade.realized_pnl = realized_pnl
        trade.realized_pnl_pct = (
            realized_pnl / trade.account_balance_at_entry if trade.account_balance_at_entry else 0.0
        )
        trade.status = "CLOSED"
        trade.updated_at = datetime.now(timezone.utc)

        self.session.add(trade)
        self.session.commit()
        self.session.refresh(trade)
        return trade

    def get_open_trades(self, symbol: str | None = None) -> list[Trade]:
        stmt = select(Trade).where(Trade.status == "OPEN")
        if symbol is not None:
            stmt = stmt.where(Trade.symbol == symbol)
        return list(self.session.exec(stmt))

    def get_open_position_count(self) -> int:
        return len(self.get_open_trades())

    def get_closed_trades_for_date(self, for_date: date) -> list[Trade]:
        stmt = select(Trade).where(Trade.status == "CLOSED")
        result = []
        for trade in self.session.exec(stmt):
            if trade.exit_time is not None and _as_utc(trade.exit_time).date() == for_date:
                result.append(trade)
        return result

    def get_realized_pnl_pct_for_date(self, for_date: date) -> float:
        """O UTC gunu kapanan islemlerin realized_pnl_pct toplami.

        Not: bu, tam bir compounded equity-curve degil -- her trade kendi
        acilis anindaki hesap bakiyesine gore yuzdelenip toplanir. v1 icin
        yeterli bir yaklastirmadir (risk/target_sizing.py bunu kullanir)."""
        trades = self.get_closed_trades_for_date(for_date)
        return sum(t.realized_pnl_pct or 0.0 for t in trades)

    def get_daily_report(self, start: date, end: date) -> list["DailyReportRow"]:
        stmt = select(Trade).where(Trade.status == "CLOSED")
        by_day: dict[date, list[Trade]] = defaultdict(list)
        for trade in self.session.exec(stmt):
            if trade.exit_time is None:
                continue
            day = _as_utc(trade.exit_time).date()
            if start <= day <= end:
                by_day[day].append(trade)

        rows = []
        for day in sorted(by_day):
            trades = by_day[day]
            wins = sum(1 for t in trades if (t.realized_pnl or 0.0) > 0)
            losses = sum(1 for t in trades if (t.realized_pnl or 0.0) <= 0)
            daily_pnl_pct = sum(t.realized_pnl_pct or 0.0 for t in trades)
            rows.append(
                DailyReportRow(
                    date=day,
                    trade_count=len(trades),
                    win_count=wins,
                    loss_count=losses,
                    daily_pnl_pct=daily_pnl_pct,
                )
            )
        return rows


@dataclass
class DailyReportRow:
    date: date
    trade_count: int
    win_count: int
    loss_count: int
    daily_pnl_pct: float


class RiskEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_event(
        self,
        event_type: str,
        reason: str | None = None,
        realized_pnl_pct_at_event: float | None = None,
        timestamp: datetime | None = None,
    ) -> RiskEvent:
        event = RiskEvent(
            event_type=event_type,
            reason=reason,
            realized_pnl_pct_at_event=realized_pnl_pct_at_event,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return event

    def get_latest_event(self, event_types: tuple[str, ...]) -> RiskEvent | None:
        stmt = (
            select(RiskEvent)
            .where(RiskEvent.event_type.in_(event_types))
            .order_by(RiskEvent.timestamp.desc(), RiskEvent.id.desc())
        )
        return self.session.exec(stmt).first()

    def is_trading_enabled(self) -> bool:
        latest = self.get_latest_event(_ACTIVE_FLAG_EVENTS["trading_enabled"])
        return latest is not None and latest.event_type == "TRADING_ENABLED"

    def is_kill_switch_tripped(self) -> bool:
        latest = self.get_latest_event(_ACTIVE_FLAG_EVENTS["kill_switch"])
        return latest is not None and latest.event_type == "KILL_SWITCH_TRIP"

    def get_kill_switch_reason(self) -> str:
        latest = self.get_latest_event(_ACTIVE_FLAG_EVENTS["kill_switch"])
        if latest is not None and latest.event_type == "KILL_SWITCH_TRIP":
            return latest.reason or ""
        return ""

    def is_first_order_confirmed(self) -> bool:
        return self.get_latest_event(("FIRST_ORDER_CONFIRMED",)) is not None
