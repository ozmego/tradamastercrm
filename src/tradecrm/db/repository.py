from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlmodel import Session, select

from tradecrm.db.models import Account, EquitySnapshot, Order, RiskEvent, Trade


def _as_utc(dt: datetime) -> datetime:
    """SQLite/SQLAlchemy datetime'lari round-trip'te tzinfo'yu kaybeder
    (naive olarak geri doner). Sistemdeki her datetime UTC olarak yazildigi
    icin, naive gelen degerler dogrudan UTC kabul edilir -- .astimezone()
    naive bir datetime'i yanlislikla YEREL saat varsayip kaydirir."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


_ACTIVE_FLAG_EVENTS = {
    "trading_enabled": ("TRADING_ENABLED", "TRADING_DISABLED"),
    "kill_switch": ("KILL_SWITCH_TRIP", "KILL_SWITCH_RESET"),
}


def _broker_clause(broker: str):
    """'paper' -> tam eslesme; 'paper*' -> onek (tum paper alt hesaplari)."""
    if broker.endswith("*"):
        return Trade.broker.startswith(broker[:-1])
    return Trade.broker == broker


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
        entry_fees: float = 0.0,
        entry_client_order_id: str | None = None,
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
            entry_fees=entry_fees,
            entry_client_order_id=entry_client_order_id,
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
        exit_reason: str | None = None,
        exit_client_order_id: str | None = None,
    ) -> Trade:
        """fees: CIKIS komisyonu. Giris komisyonu (trade.entry_fees) de P&L'den
        dusulur -- aksi halde her islemin kari giris komisyonu kadar fazla
        gorunur (kill-switch ve sizing bu P&L'i kullanir)."""
        trade = self.session.get(Trade, trade_id)
        if trade is None:
            raise KeyError(f"Trade bulunamadi: {trade_id}")
        if trade.status == "CLOSED":
            raise ValueError(f"Trade zaten kapali: {trade_id}")

        gross_pnl = (exit_price - trade.entry_price) * trade.qty
        total_fees = fees + (trade.entry_fees or 0.0)
        realized_pnl = gross_pnl - total_fees

        trade.exit_time = exit_time
        trade.exit_price = exit_price
        trade.fees = total_fees
        trade.exit_reason = exit_reason
        trade.exit_client_order_id = exit_client_order_id
        trade.realized_pnl = realized_pnl
        trade.realized_pnl_pct = (
            realized_pnl / trade.account_balance_at_entry if trade.account_balance_at_entry else 0.0
        )
        trade.status = "CLOSED"
        trade.updated_at = datetime.now(UTC)

        self.session.add(trade)
        self.session.commit()
        self.session.refresh(trade)
        return trade

    def update_exit_levels(
        self,
        trade_id: int,
        stop_price: float | None,
        take_profit_price: float | None,
        highest_price: float | None,
        initial_stop_price: float | None = None,
    ) -> Trade:
        trade = self.session.get(Trade, trade_id)
        if trade is None:
            raise KeyError(f"Trade bulunamadi: {trade_id}")
        trade.stop_price = stop_price
        trade.take_profit_price = take_profit_price
        trade.highest_price = highest_price
        if initial_stop_price is not None:
            trade.initial_stop_price = initial_stop_price
        trade.updated_at = datetime.now(UTC)
        self.session.add(trade)
        self.session.commit()
        self.session.refresh(trade)
        return trade

    def get_open_trades(self, symbol: str | None = None, broker: str | None = None) -> list[Trade]:
        stmt = select(Trade).where(Trade.status == "OPEN")
        if symbol is not None:
            stmt = stmt.where(Trade.symbol == symbol)
        if broker is not None:
            stmt = stmt.where(_broker_clause(broker))
        return list(self.session.exec(stmt))

    def get_open_position_count(self, broker: str | None = None) -> int:
        return len(self.get_open_trades(broker=broker))

    def get_closed_trades_between(
        self, start: datetime, end: datetime, broker: str | None = None
    ) -> list[Trade]:
        """[start, end) araliginda kapanan islemler, exit_time'a gore sirali.
        Filtre SQL'de yapilir (her tick'te butun tabloyu cekmemek icin)."""
        stmt = (
            select(Trade)
            .where(Trade.status == "CLOSED", Trade.exit_time >= start, Trade.exit_time < end)
            .order_by(Trade.exit_time)
        )
        if broker is not None:
            stmt = stmt.where(_broker_clause(broker))
        return list(self.session.exec(stmt))

    def get_closed_trades_for_date(self, for_date: date, broker: str | None = None) -> list[Trade]:
        start = datetime.combine(for_date, time.min, tzinfo=UTC)
        return self.get_closed_trades_between(start, start + timedelta(days=1), broker=broker)

    def get_all_closed_trades(self, broker: str | None = None) -> list[Trade]:
        stmt = select(Trade).where(Trade.status == "CLOSED").order_by(Trade.exit_time)
        if broker is not None:
            stmt = stmt.where(_broker_clause(broker))
        return list(self.session.exec(stmt))

    def get_realized_pnl_pct_for_date(self, for_date: date, broker: str | None = None) -> float:
        """O UTC gunu kapanan islemlerin realized_pnl_pct toplami.

        Not: bu, tam bir compounded equity-curve degil -- her trade kendi
        acilis anindaki hesap bakiyesine gore yuzdelenip toplanir. v1 icin
        yeterli bir yaklastirmadir (risk/target_sizing.py bunu kullanir)."""
        trades = self.get_closed_trades_for_date(for_date, broker=broker)
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


def root_account(account: str) -> str:
    """'paper:donchian' -> 'paper'; 'binance' -> 'binance'."""
    return account.rstrip("*").split(":", 1)[0]


class RiskEventRepository:
    """Hesap bazli risk bayraklari.

    - TRADING_ENABLED: kok hesap (orn. 'paper') VE alt hesap ('paper:x')
      birlikte izin vermeli. Alt hesabin kendi olayi yoksa kokunu izler.
      Koku kapatmak butun alt hesaplari kapatir.
    - Kill-switch: kokte YA DA alt hesapta tetiklenmisse tetiklenmis sayilir.
    - Ilk canli emir onayi: hesap ya da kok icin kaydedilmisse gecerli.
    """

    def __init__(self, session: Session, account: str = "paper") -> None:
        self.session = session
        self.account = account.rstrip("*")

    def _accounts(self) -> list[str]:
        root = root_account(self.account)
        return [root] if root == self.account else [root, self.account]

    def record_event(
        self,
        event_type: str,
        reason: str | None = None,
        realized_pnl_pct_at_event: float | None = None,
        timestamp: datetime | None = None,
        account: str | None = None,
    ) -> RiskEvent:
        event = RiskEvent(
            event_type=event_type,
            account=account or self.account,
            reason=reason,
            realized_pnl_pct_at_event=realized_pnl_pct_at_event,
            timestamp=timestamp or datetime.now(UTC),
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        return event

    def get_latest_event(self, event_types: tuple[str, ...], account: str | None = None) -> RiskEvent | None:
        stmt = (
            select(RiskEvent)
            .where(RiskEvent.event_type.in_(event_types), RiskEvent.account == (account or self.account))
            .order_by(RiskEvent.timestamp.desc(), RiskEvent.id.desc())
        )
        return self.session.exec(stmt).first()

    def is_trading_enabled(self) -> bool:
        for acc in self._accounts():
            latest = self.get_latest_event(_ACTIVE_FLAG_EVENTS["trading_enabled"], acc)
            is_root = acc == root_account(self.account)
            if latest is None:
                if is_root:
                    return False  # kok hic acilmamis -> kapali (varsayilan)
                continue  # alt hesabin kendi karari yok -> kokunu izler
            if latest.event_type != "TRADING_ENABLED":
                return False
        return True

    def _tripped_event(self) -> RiskEvent | None:
        for acc in self._accounts():
            latest = self.get_latest_event(_ACTIVE_FLAG_EVENTS["kill_switch"], acc)
            if latest is not None and latest.event_type == "KILL_SWITCH_TRIP":
                return latest
        return None

    def is_kill_switch_tripped(self) -> bool:
        return self._tripped_event() is not None

    def get_kill_switch_reason(self) -> str:
        ev = self._tripped_event()
        if ev is None:
            return ""
        # Kokun kill-switch'i bir alt hesabi etkiliyorsa kaynagi belirt.
        return ev.reason or "" if ev.account == self.account else f"[{ev.account}] {ev.reason or ''}"

    def last_event_time(self, event_types: tuple[str, ...]) -> datetime | None:
        """Hesap ya da kokundeki en son olay zamani."""
        times = [
            ev.timestamp
            for acc in self._accounts()
            if (ev := self.get_latest_event(event_types, acc)) is not None
        ]
        return max(times) if times else None

    def last_reset_time(self) -> datetime | None:
        return self.last_event_time(("KILL_SWITCH_RESET",))

    def is_first_order_confirmed(self) -> bool:
        return any(self.get_latest_event(("FIRST_ORDER_CONFIRMED",), acc) is not None for acc in self._accounts())


class DuplicateOrderError(Exception):
    """Ayni (hesap, sembol, yon, mum) icin zaten bir emir var."""


def make_client_order_id(account: str, symbol: str, side: str, bar_time: datetime) -> str:
    """Deterministik, Binance newClientOrderId uyumlu (<=36, [A-Za-z0-9_-])."""
    import hashlib

    key = f"{account}|{symbol}|{side}|{_as_utc(bar_time).isoformat()}"
    return "tc-" + hashlib.sha256(key.encode()).hexdigest()[:32]


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def reserve(
        self, account: str, symbol: str, side: str, bar_time: datetime,
        reason: str | None = None, requested_notional: float | None = None,
    ) -> Order:
        """Emri NEW olarak kaydeder. Ayni niyet daha once kaydedilmisse
        DuplicateOrderError -- benzersizlik kisiti veritabaninda oldugu icin
        eszamanli iki runner'dan da sadece biri kazanir."""
        from sqlalchemy.exc import IntegrityError

        order = Order(
            client_order_id=make_client_order_id(account, symbol, side, bar_time),
            account=account, symbol=symbol, side=side, bar_time=bar_time, status="NEW",
            reason=reason, requested_notional=requested_notional,
        )
        self.session.add(order)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            raise DuplicateOrderError(f"{account} {symbol} {side} {bar_time}") from None
        self.session.refresh(order)
        return order

    def update(self, order: Order, **fields) -> Order:
        for k, v in fields.items():
            setattr(order, k, v)
        order.updated_at = datetime.now(UTC)
        self.session.add(order)
        self.session.commit()
        self.session.refresh(order)
        return order

    def get(self, client_order_id: str) -> Order | None:
        return self.session.exec(select(Order).where(Order.client_order_id == client_order_id)).first()

    def unresolved(self, account: str) -> list[Order]:
        """Sonucu kesinlesmemis emirler (NEW / UNKNOWN)."""
        stmt = select(Order).where(Order.account == account, Order.status.in_(("NEW", "UNKNOWN"))).order_by(Order.id)
        return list(self.session.exec(stmt))


# ------------------------------------------------------------------ hesaplar
class AccountMismatchError(ValueError):
    """Kayitli hesap farkli sermaye/mod ile yeniden baslatilmak istendi."""


class AccountRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, name: str) -> Account | None:
        return self.session.exec(select(Account).where(Account.name == name)).first()

    def all(self) -> list[Account]:
        return list(self.session.exec(select(Account).order_by(Account.name)))

    def register(
        self, name: str, initial_capital: float, mode: str = "paper", profile: str | None = None,
        timeframe: str | None = None,
    ) -> Account:
        """Hesabi ilk kez kaydeder; varsa ayni sermaye ve modla oldugunu dogrular.
        Paper nakdi baslangic sermayesinden turetildigi icin farkli sermayeyle
        yeniden baslatmak gecmis muhasebeyi bozar -> AccountMismatchError."""
        existing = self.get(name)
        if existing is not None:
            if existing.mode != mode or abs(existing.initial_capital - initial_capital) > 1e-6:
                raise AccountMismatchError(
                    f"{name} hesabi {existing.mode} / {existing.initial_capital:,.2f} ile kayitli; "
                    f"{mode} / {initial_capital:,.2f} ile baslatilamaz"
                )
            if (profile and existing.profile != profile) or (timeframe and existing.timeframe != timeframe):
                existing.profile = profile or existing.profile
                existing.timeframe = timeframe or existing.timeframe
                self.session.add(existing)
                self.session.commit()
            return existing
        account = Account(name=name, mode=mode, initial_capital=initial_capital, profile=profile, timeframe=timeframe)
        self.session.add(account)
        self.session.commit()
        self.session.refresh(account)
        return account

    def known_names(self) -> list[str]:
        """Kayitli hesaplar + gecmiste islem/olay gormus ama kaydi olmayanlar (eski veriler)."""
        names = {a.name for a in self.all()}
        names |= set(self.session.exec(select(Trade.broker).distinct()))
        names |= set(self.session.exec(select(RiskEvent.account).distinct()))
        return sorted(n for n in names if n)


class EquityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, account: str, snapshot, ts: datetime | None = None) -> EquitySnapshot:
        row = EquitySnapshot(
            account=account, ts=ts or datetime.now(UTC), cash=snapshot.cash, allocated=snapshot.allocated,
            market_value=snapshot.market_value, equity=snapshot.equity, open_positions=len(snapshot.positions),
        )
        self.session.add(row)
        self.session.commit()
        return row

    def series(self, account: str, since: datetime | None = None) -> list[EquitySnapshot]:
        stmt = select(EquitySnapshot).where(EquitySnapshot.account == account)
        if since is not None:
            stmt = stmt.where(EquitySnapshot.ts >= since)
        return list(self.session.exec(stmt.order_by(EquitySnapshot.ts)))

    def latest(self, account: str) -> EquitySnapshot | None:
        return self.session.exec(
            select(EquitySnapshot).where(EquitySnapshot.account == account).order_by(EquitySnapshot.ts.desc())
        ).first()
