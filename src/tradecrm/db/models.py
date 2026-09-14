from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tz_column(*, index: bool = False):
    return Column(DateTime(timezone=True), index=index)


class Trade(SQLModel, table=True):
    """Gerceklesen bir fill/pozisyon kaydi (paper veya live).

    realized_pnl_pct, trade acilirken hesaba anlik bakiyeye (account_balance_at_entry)
    gore hesaplanir -- islem hacmine gore degil, hesap buyumesine gore. Gunluk
    hedef takibi (risk/target_sizing) bu yuzdelerin gun icinde toplanmasiyla
    yapilir; bu, tam bir compounded equity-curve degil, v1 icin yeterli bir
    yaklastirmadir.
    """

    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    side: str = "BUY"  # spot/long-only v1: giris hep BUY, cikis SELL ile temsil edilir (status/exit alanlarinda)
    strategy_name: str
    broker: str  # "paper" | "binance"

    entry_time: datetime = Field(sa_column=_tz_column(index=True))
    entry_price: float
    qty: float
    account_balance_at_entry: float

    exit_time: datetime | None = Field(default=None, sa_column=_tz_column(index=True))
    exit_price: float | None = None

    status: str = Field(default="OPEN", index=True)  # "OPEN" | "CLOSED"
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None
    fees: float = 0.0
    signal_reason: str | None = None

    created_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_tz_column())


class RiskEvent(SQLModel, table=True):
    """Kill-switch / TRADING_ENABLED / ilk-emir-onayi gibi risk durumu
    gecislerinin kaydi. Bir bayragin *anlik durumu*, o bayrak tipi icin
    en son event satiridir -- ayri bir dosya/tablo yok."""

    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)  # KILL_SWITCH_TRIP | KILL_SWITCH_RESET | TRADING_ENABLED | TRADING_DISABLED | FIRST_ORDER_CONFIRMED
    timestamp: datetime = Field(default_factory=_utcnow, sa_column=_tz_column(index=True))
    reason: str | None = None
    realized_pnl_pct_at_event: float | None = None
