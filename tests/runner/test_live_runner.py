"""LiveRunner: basarili akis + hata senaryolari.

Gercek OrderManager ve RiskManager kullanilir; borsa (FakeBroker) ve emir
deposu (InMemoryOrders) bellek icidir. DB seviyesindeki tekillik ve
eszamanlilik testleri tests/db/test_orders.py'de.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tradecrm.broker.base import (
    AccountSnapshot,
    BrokerError,
    BrokerTimeoutError,
    OrderResult,
    PositionView,
)
from tradecrm.broker.order_manager import OrderManager
from tradecrm.db.models import Order
from tradecrm.db.repository import DuplicateOrderError, make_client_order_id
from tradecrm.risk.breakers import BreakerConfig
from tradecrm.risk.exits import ExitConfig
from tradecrm.risk.guardrails import RiskManager
from tradecrm.risk.position_sizing import SizingConfig
from tradecrm.runner.live_runner import LiveRunner, MultiRunner
from tradecrm.strategy.base import Signal


# ----------------------------------------------------------------------- stubs
def _df(n=40, close=100.0, last_low=None, age_minutes=1):
    """Kapanmis 1h barlar; son barin kapanisi `age_minutes` dakika once."""
    end_close = datetime.now(UTC) - timedelta(minutes=age_minutes)
    idx = pd.date_range(end=end_close - timedelta(hours=1), periods=n, freq="h", tz="UTC")
    df = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1.0}, index=idx)
    if last_low is not None:
        df.iloc[-1, df.columns.get_loc("Low")] = last_low
    return df


class _Provider:
    def __init__(self, df=None, price=100.0, should_raise=False, price_raises=False):
        self.df = df if df is not None else _df()
        self.price = price
        self.should_raise = should_raise
        self.price_raises = price_raises

    def fetch_latest_ohlcv(self, symbol, timeframe, limit):
        if self.should_raise:
            raise ConnectionError("borsa verisi cekilemedi")
        return self.df

    def fetch_latest_price(self, symbol):
        if self.price_raises:
            raise ConnectionError("ticker yok")
        return self.price


class _Strategy:
    name = "stub_strategy"

    def __init__(self, signal):
        self.signal = signal

    def generate_signal(self, df):
        return self.signal


@dataclass
class _Trade:
    id: int
    entry_time: datetime
    entry_price: float
    qty: float
    account_balance_at_entry: float
    initial_stop_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    highest_price: float | None = None
    exit_time: datetime | None = None
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None


class FakeBroker:
    """Bellek ici borsa. mode:
    - None                : normal
    - "timeout_after_fill": emir olusur ama yanit kaybolur (BrokerTimeoutError)
    - "timeout_no_fill"   : ilk deneme borsaya ulasmaz; ikinci deneme basarili
    - "timeout_query_fail": timeout + sorgu da basarisiz
    - "reject"            : kesin ret
    """

    name = "paper"

    def __init__(self, cash=10_000.0, price=100.0, position=None, mode=None):
        self.cash = cash
        self.price = price
        self.position = position
        self.mode = mode
        self.orders: dict[str, OrderResult] = {}
        self.submits: list[tuple] = []
        self.snapshot_raises = False
        self.query_raises = False
        self._first_attempt_done = False

    def get_snapshot(self):
        if self.snapshot_raises:
            raise ConnectionError("bakiye alinamadi")
        positions = []
        if self.position is not None:
            p = self.position
            positions.append(PositionView("BTC/USDT", p.qty, p.entry_price * p.qty, self.price, p.qty * self.price, p.id))
        return AccountSnapshot(self.cash, sum(x.cost for x in positions), sum(x.market_value for x in positions), positions)

    def get_open_position(self, symbol):
        return self.position

    def get_balance(self):
        return self.get_snapshot().realized_equity

    def _fill(self, coid, side, notional):
        if side is Signal.BUY:
            qty = notional / self.price
            self.cash -= notional
            self.position = _Trade(7, datetime.now(UTC), self.price, qty, 10_000.0)
            res = OrderResult(True, "filled", 7, self.price, qty, coid)
        else:
            if self.position is None:
                return OrderResult(False, "no_open_position", client_order_id=coid)
            self.cash += self.position.qty * self.price
            res = OrderResult(True, "closed", self.position.id, self.price, self.position.qty, coid)
            self.position = None
        self.orders[coid] = res
        return res

    def submit_order(self, client_order_id, symbol, side, notional, strategy_name, reason=None):
        self.submits.append((client_order_id, side, notional, reason))
        if client_order_id in self.orders:
            return self.orders[client_order_id]
        if self.mode == "reject":
            raise BrokerError("insufficient balance")
        if self.mode == "timeout_after_fill":
            self._fill(client_order_id, side, notional)
            raise BrokerTimeoutError("read timeout")
        if self.mode == "timeout_no_fill" and not self._first_attempt_done:
            self._first_attempt_done = True
            raise BrokerTimeoutError("connect timeout")
        if self.mode == "timeout_query_fail":
            self.query_raises = True
            raise BrokerTimeoutError("read timeout")
        return self._fill(client_order_id, side, notional)

    def get_order(self, client_order_id):
        if self.query_raises:
            raise ConnectionError("sorgu basarisiz")
        return self.orders.get(client_order_id)

    def reconcile_positions(self):
        return []


class InMemoryOrders:
    """OrderRepository'nin bellek ici esdegeri (ayni tekillik kurali)."""

    def __init__(self):
        self.rows: list[Order] = []

    def reserve(self, account, symbol, side, bar_time, reason=None, requested_notional=None):
        for o in self.rows:
            if (o.account, o.symbol, o.side, o.bar_time) == (account, symbol, side, bar_time):
                raise DuplicateOrderError(symbol)
        order = Order(
            client_order_id=make_client_order_id(account, symbol, side, bar_time), account=account, symbol=symbol,
            side=side, bar_time=bar_time, status="NEW", reason=reason, requested_notional=requested_notional,
            created_at=datetime.now(UTC),
        )
        self.rows.append(order)
        return order

    def update(self, order, **fields):
        for k, v in fields.items():
            setattr(order, k, v)
        return order

    def get(self, coid):
        return next((o for o in self.rows if o.client_order_id == coid), None)

    def unresolved(self, account):
        return [o for o in self.rows if o.account == account and o.status in ("NEW", "UNKNOWN")]


class _State:
    is_live = False

    def __init__(self, realized_pnl_pct_today=0.0, closed=None, trading_enabled=True):
        self._pnl = realized_pnl_pct_today
        self.closed = closed or []
        self.trading_enabled = trading_enabled
        self.kill_tripped = False
        self.kill_switch_reason = ""
        self.level_updates = []

    def get_realized_pnl_pct(self, for_date):
        return self._pnl

    def is_trading_enabled(self):
        return self.trading_enabled

    def is_kill_switch_tripped(self):
        return self.kill_tripped

    def is_first_order_confirmed(self):
        return False

    def trip_kill_switch(self, reason):
        self.kill_tripped, self.kill_switch_reason = True, reason

    def get_closed_trades_since_last_reset(self):
        return self.closed

    def update_exit_levels(self, trade_id, **levels):
        self.level_updates.append((trade_id, levels))


class _Notifier:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return True


class _Audit:
    def __init__(self):
        self.entries = []

    def log(self, event_type, **fields):
        self.entries.append((event_type, fields))

    def types(self):
        return [e for e, _ in self.entries]

    def last(self, event_type):
        return next(f for e, f in reversed(self.entries) if e == event_type)


def make_runner(signal, broker=None, provider=None, state=None, orders=None, **overrides):
    broker = broker or FakeBroker()
    state = state or _State()
    orders = orders if orders is not None else InMemoryOrders()
    kwargs = {
        "data_provider": provider or _Provider(),
        "strategy": _Strategy(signal),
        "risk_manager": RiskManager(state, total_capital_allocation_pct=0.75),
        "state": state,
        "broker": broker,
        "notifier": _Notifier(),
        "audit_logger": _Audit(),
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "sizing": SizingConfig(mode="fixed", fixed_fraction=0.75),
        "order_manager": OrderManager(broker, orders),
    }
    kwargs.update(overrides)
    return LiveRunner(**kwargs)


def _position(df, **kw):
    defaults = {
        "id": 3, "entry_time": df.index[-5].to_pydatetime(), "entry_price": 100.0, "qty": 10.0,
        "account_balance_at_entry": 10_000.0, "initial_stop_price": 96.0, "stop_price": 96.0,
        "take_profit_price": None, "highest_price": 100.0,
    }
    defaults.update(kw)
    return _Trade(**defaults)


# ---------------------------------------------------------------- basarili akis
class TestHappyPath:
    def test_hold_does_nothing(self):
        r = make_runner(Signal.HOLD)
        r.run_once()
        assert r.broker.submits == []
        assert r.audit_logger.types() == ["signal_evaluated"]

    def test_buy_uses_equity_fraction_and_records_order(self):
        r = make_runner(Signal.BUY)
        r.run_once()
        coid, side, notional, _ = r.broker.submits[0]
        assert side is Signal.BUY
        assert notional == pytest.approx(7_500.0)
        assert r.order_manager.orders.rows[0].status == "FILLED"
        assert coid == r.order_manager.orders.rows[0].client_order_id
        assert len(r.notifier.messages) == 1

    def test_risk_sizing_with_exits_sets_levels(self):
        r = make_runner(Signal.BUY, sizing=SizingConfig(mode="risk", risk_per_trade_pct=0.01),
                        exits=ExitConfig(stop_atr_mult=2.0))
        r.run_once()
        _, _, notional, _ = r.broker.submits[0]
        assert notional == pytest.approx(2_500.0, rel=0.05)  # ATR=2 -> stop 4 uzakta -> %1/%4 = %25
        assert r.state.level_updates[0][1]["stop_price"] == pytest.approx(96.0, abs=0.2)

    def test_paper_needs_no_first_order_confirmation(self):
        assert make_runner(Signal.BUY).is_first_live_order is False
        live = FakeBroker()
        live.name = "binance"
        assert make_runner(Signal.BUY, broker=live).is_first_live_order is True


# ------------------------------------------------------- toplam pozisyon tavani
class TestExposureCap:
    def test_cap_applies_to_all_open_positions(self):
        """Baska sembolde 6.000'lik acik pozisyon varken (equity 10.000, tavan %75),
        yeni alim en fazla 1.500 olabilir."""
        broker = FakeBroker(cash=4_000.0)

        def snapshot():
            pos = [PositionView("ETH/USDT", 60.0, 6_000.0, 100.0, 6_000.0, 99)]
            return AccountSnapshot(broker.cash, 6_000.0, 6_000.0, pos)

        broker.get_snapshot = snapshot
        r = make_runner(Signal.BUY, broker=broker)
        r.run_once()
        assert r.broker.submits[0][2] == pytest.approx(1_500.0)
        assert r.audit_logger.last("order_result")["risk_reason"] == "ok_capped"

    def test_blocked_when_cap_reached(self):
        broker = FakeBroker(cash=2_500.0)
        broker.get_snapshot = lambda: AccountSnapshot(
            2_500.0, 7_500.0, 7_500.0, [PositionView("ETH/USDT", 75.0, 7_500.0, 100.0, 7_500.0, 99)]
        )
        r = make_runner(Signal.BUY, broker=broker)
        r.run_once()
        assert r.broker.submits == []
        assert r.audit_logger.last("order_blocked")["reason"].startswith("exposure_cap_reached")


# --------------------------------------------------- tekrar gelen sinyal / restart
class TestDuplicates:
    def test_same_bar_signal_twice_places_one_order(self):
        r = make_runner(Signal.BUY)
        r.run_once()
        r.broker.position = None  # pozisyon kapanmis olsa bile ayni mumda ikinci alim yok
        r.run_once()
        assert len(r.broker.submits) == 1

    def test_restart_does_not_duplicate(self):
        """Ayni DB (emir deposu) ile yeni bir runner sureci: ayni mum icin emir tekrarlanmaz."""
        orders, broker = InMemoryOrders(), FakeBroker()
        provider = _Provider()
        make_runner(Signal.BUY, broker=broker, orders=orders, provider=provider).run_once()
        broker.position = None
        make_runner(Signal.BUY, broker=broker, orders=orders, provider=provider).run_once()
        assert len(broker.submits) == 1

    def test_restart_after_crash_between_reserve_and_submit(self):
        """Emir NEW olarak kaydedildi, sonra surec coktu (borsaya hic gitmedi).
        Yeniden baslayinca: borsada yok + eski -> ABANDONED; ayni mum icin
        tekrar GONDERILMEZ (kor tekrar yok)."""
        orders, broker = InMemoryOrders(), FakeBroker()
        df = _df()
        order = orders.reserve("paper", "BTC/USDT", "BUY", df.index[-1].to_pydatetime())
        order.created_at = datetime.now(UTC) - timedelta(minutes=10)
        r = make_runner(Signal.BUY, broker=broker, orders=orders, provider=_Provider(df))
        r.run_once()
        assert order.status == "ABANDONED"
        assert broker.submits == []

    def test_restart_after_crash_post_fill_reconciles(self):
        """Emir borsada doldu ama DB guncellenemeden cokuldu: uzlastirma
        borsadaki fill'i bulur ve kaydeder."""
        orders, broker = InMemoryOrders(), FakeBroker()
        df = _df()
        order = orders.reserve("paper", "BTC/USDT", "BUY", df.index[-1].to_pydatetime())
        broker.orders[order.client_order_id] = OrderResult(True, "filled", 7, 100.0, 1.0, order.client_order_id)
        r = make_runner(Signal.HOLD, broker=broker, orders=orders, provider=_Provider(df))
        r.run_once()
        assert order.status == "FILLED"
        assert "orders_reconciled" in r.audit_logger.types()


# ---------------------------------------------------------- baglanti kesilmesi
class TestConnectionLoss:
    def test_timeout_after_fill_is_not_resubmitted(self):
        broker = FakeBroker(mode="timeout_after_fill")
        r = make_runner(Signal.BUY, broker=broker)
        r.run_once()
        assert len(broker.submits) == 1  # sorgu fill'i buldu, tekrar gonderilmedi
        assert r.order_manager.orders.rows[0].status == "FILLED"

    def test_timeout_without_fill_retries_with_same_id(self):
        broker = FakeBroker(mode="timeout_no_fill")
        r = make_runner(Signal.BUY, broker=broker)
        r.run_once()
        assert len(broker.submits) == 2
        assert broker.submits[0][0] == broker.submits[1][0]  # ayni client_order_id
        assert r.order_manager.orders.rows[0].status == "FILLED"
        assert r.order_manager.orders.rows[0].attempts == 2

    def test_unknown_order_blocks_new_buys_until_reconciled(self):
        broker = FakeBroker(mode="timeout_query_fail")
        r = make_runner(Signal.BUY, broker=broker)
        r.run_once()
        order = r.order_manager.orders.rows[0]
        assert order.status == "UNKNOWN"
        assert any("BELIRSIZ" in m for m in r.notifier.messages)

        # Sonraki mum (farkli mum zamani): sorgu hala calismiyor -> hesap belirsiz -> alim yok
        r.data_provider.df = _df(age_minutes=0.8)
        r.run_once()
        assert len(broker.submits) == 1
        assert "account_uncertain" in r.audit_logger.last("order_blocked")["reason"]

        # Baglanti geri geldi; borsa emri gormemis -> ABANDONED; alimlar serbest
        broker.query_raises = False
        broker.mode = None
        r.data_provider.df = _df(age_minutes=0.6)
        r.run_once()
        assert order.status == "ABANDONED"
        assert len(broker.submits) == 2
        assert any("tekrar kesin" in m for m in r.notifier.messages)

    def test_rejected_order_recorded(self):
        r = make_runner(Signal.BUY, broker=FakeBroker(mode="reject"))
        r.run_once()
        assert r.order_manager.orders.rows[0].status == "REJECTED"
        assert r.notifier.messages == []

    def test_snapshot_failure_blocks_buy(self):
        broker = FakeBroker()
        broker.snapshot_raises = True
        r = make_runner(Signal.BUY, broker=broker)
        r.run_once()
        assert broker.submits == []
        assert "snapshot_failed" in r.audit_logger.last("order_blocked")["reason"]


# ------------------------------------------------- eksik / bayat / gecersiz veri
class TestDataQuality:
    @pytest.mark.parametrize(
        ("provider_kwargs", "expected"),
        [
            ({"df": "stale"}, "stale_signal"),
            ({"price": float("nan")}, "invalid_price"),
            ({"price": 150.0}, "price_deviation"),
            ({"price_raises": True}, "ticker_unavailable"),
        ],
        ids=["stale", "nan_price", "deviation", "no_ticker"],
    )
    def test_bad_data_blocks_buy(self, provider_kwargs, expected):
        if provider_kwargs.get("df") == "stale":
            provider_kwargs = {"df": _df(age_minutes=45)}
        r = make_runner(Signal.BUY, provider=_Provider(**provider_kwargs))
        r.run_once()
        assert r.broker.submits == []
        assert expected in r.audit_logger.last("order_blocked")["reason"]

    def test_gap_in_data_blocks_buy(self):
        df = _df(n=40)
        df = df.drop(df.index[20])
        r = make_runner(Signal.BUY, provider=_Provider(df))
        r.run_once()
        assert "data_gap" in r.audit_logger.last("order_blocked")["reason"]

    def test_invalid_ohlc_blocks_buy(self):
        df = _df()
        df.iloc[-3, df.columns.get_loc("Close")] = np.nan
        r = make_runner(Signal.BUY, provider=_Provider(df))
        r.run_once()
        assert "invalid_ohlc" in r.audit_logger.last("order_blocked")["reason"]

    def test_no_data_does_nothing(self):
        r = make_runner(Signal.BUY, provider=_Provider(df=_df().iloc[:0]))
        r.run_once()
        assert r.audit_logger.types() == ["no_data"]

    def test_sell_still_allowed_with_bad_data(self):
        """Veri belirsizken bile cikis yolu acik."""
        df = _df(age_minutes=45)
        broker = FakeBroker(position=_position(df))
        r = make_runner(Signal.SELL, broker=broker, provider=_Provider(df, price=float("nan")))
        r.run_once()
        assert broker.submits[0][1] is Signal.SELL

    def test_sell_allowed_when_account_uncertain(self):
        df = _df()
        broker = FakeBroker(position=_position(df))
        broker.snapshot_raises = True
        r = make_runner(Signal.SELL, broker=broker, provider=_Provider(df))
        r.run_once()
        assert broker.submits[0][1] is Signal.SELL

    def test_invalid_last_bar_does_not_trigger_false_stop(self):
        df = _df(last_low=0.0)  # hatali veri: Low=0 stop'u tetiklememeli
        broker = FakeBroker(position=_position(df))
        r = make_runner(Signal.HOLD, broker=broker, provider=_Provider(df),
                        exits=ExitConfig(stop_atr_mult=2.0, take_profit_atr_mult=None))
        r.run_once()
        assert broker.submits == []
        assert "exit_check_skipped" in r.audit_logger.types()


# --------------------------------------------------------------- acik pozisyon
class TestOpenPosition:
    def test_stop_hit_sells(self):
        df = _df(last_low=95.0)
        broker = FakeBroker(position=_position(df))
        r = make_runner(Signal.HOLD, broker=broker, provider=_Provider(df),
                        exits=ExitConfig(stop_atr_mult=2.0, take_profit_atr_mult=None))
        r.run_once()
        assert broker.submits[0][1] is Signal.SELL
        assert broker.submits[0][3] == "stop_loss"

    def test_buy_signal_ignored_while_in_position(self):
        df = _df()
        broker = FakeBroker(position=_position(df))
        make_runner(Signal.BUY, broker=broker, provider=_Provider(df)).run_once()
        assert broker.submits == []

    def test_time_stop(self):
        df = _df()
        broker = FakeBroker(position=_position(df))
        r = make_runner(Signal.HOLD, broker=broker, provider=_Provider(df),
                        exits=ExitConfig(stop_atr_mult=2.0, take_profit_atr_mult=None, max_bars_held=3))
        r.run_once()
        assert broker.submits[0][3] == "time_stop"

    def test_daily_loss_counts_unrealized_of_all_positions(self):
        df = _df(close=90.0)
        broker = FakeBroker(cash=9_000.0, price=90.0, position=_position(df, initial_stop_price=None))
        state = _State()
        r = make_runner(Signal.HOLD, broker=broker, provider=_Provider(df, price=90.0), state=state)
        r.risk_manager.max_daily_loss_pct = 0.005
        r.run_once()
        # (90-100)*10 = -100 gerceklesmemis; gerceklesen equity 10.000 -> -%1 <= -%0.5 -> kill-switch
        assert state.kill_tripped is True
        assert r.audit_logger.last("signal_evaluated")["unrealized_pnl_pct"] == pytest.approx(-0.01)


# ------------------------------------------------------------------ devre kesici
class TestBreakers:
    def test_loss_streak_blocks_entry(self):
        now = datetime.now(UTC)
        closed = [
            _Trade(i, now, 100, 1, 10_000, exit_time=now - timedelta(hours=3 - i), realized_pnl=-10,
                   realized_pnl_pct=-0.001)
            for i in range(3)
        ]
        r = make_runner(Signal.BUY, state=_State(closed=closed), breakers=BreakerConfig())
        r.run_once()
        assert r.broker.submits == []
        assert r.audit_logger.last("order_blocked")["reason"].startswith("loss_streak")

    def test_drawdown_trips_kill_switch(self):
        now = datetime.now(UTC)
        closed = [_Trade(1, now, 100, 1, 10_000, exit_time=now - timedelta(days=2), realized_pnl=-2_000,
                         realized_pnl_pct=-0.2)]
        state = _State(closed=closed)
        r = make_runner(Signal.BUY, broker=FakeBroker(cash=8_000.0), state=state,
                        breakers=BreakerConfig(max_consecutive_losses=None, max_weekly_loss_pct=None))
        r.run_once()
        assert state.kill_tripped is True
        assert "max_drawdown" in state.kill_switch_reason

    def test_daily_target_blocks_entry_without_kill_switch(self):
        now = datetime.now(UTC)
        closed = [
            _Trade(i, now, 100, 1, 10_000, exit_time=now - timedelta(minutes=10 - i), realized_pnl=300,
                   realized_pnl_pct=0.03)
            for i in range(2)
        ]
        state = _State(closed=closed)
        r = make_runner(Signal.BUY, state=state,
                        breakers=BreakerConfig(max_consecutive_losses=None, daily_profit_target_pct=0.05))
        r.run_once()
        assert r.broker.submits == []
        assert r.audit_logger.last("order_blocked")["reason"].startswith("daily_target_reached")
        assert state.kill_tripped is False

    def test_trading_disabled_blocks(self):
        r = make_runner(Signal.BUY, state=_State(trading_enabled=False))
        r.run_once()
        assert r.audit_logger.last("order_blocked")["reason"] == "trading_disabled"


# ------------------------------------------------------------------- hatalar
class TestErrors:
    def test_run_once_propagates_exceptions(self):
        r = make_runner(Signal.HOLD, provider=_Provider(should_raise=True))
        with pytest.raises(ConnectionError):
            r.run_once()

    def test_safe_tick_catches_and_rolls_back(self):
        class _Session:
            rolled_back = 0

            def rollback(self):
                self.rolled_back += 1

        session = _Session()
        r = make_runner(Signal.HOLD, provider=_Provider(should_raise=True), session=session)
        r.safe_tick()
        assert session.rolled_back == 1
        assert "tick_error" in r.audit_logger.types()
        assert any("HATA" in m for m in r.notifier.messages)

    def test_db_error_during_reserve_sends_no_order(self):
        """Emir DB'ye yazilamazsa borsaya GONDERILMEZ (kayitsiz emir yok)."""

        class _BrokenOrders(InMemoryOrders):
            def reserve(self, *a, **k):
                raise RuntimeError("DB baglantisi koptu")

        r = make_runner(Signal.BUY, orders=_BrokenOrders())
        r.safe_tick()
        assert r.broker.submits == []
        assert "tick_error" in r.audit_logger.types()

    def test_multi_runner_isolates_member_errors(self):
        bad = make_runner(Signal.HOLD, provider=_Provider(should_raise=True))
        good = make_runner(Signal.BUY)
        MultiRunner([bad, good]).safe_tick()
        assert "tick_error" in bad.audit_logger.types()
        assert len(good.broker.submits) == 1


# ------------------------------------------------- coklu sembol, ortak hesap
class TestMultiSymbolSharedAccount:
    """Ayni hesapta sembol basina bir runner: nakit, %75 toplam tavan ve
    belirsizlik bildirimi hesap geneli olmali (gercek DB + paper broker)."""

    SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")

    def _runners(self, db_session, broker=None, sizing_fraction=0.5):
        from tradecrm.broker.paper_adapter import PaperBrokerAdapter
        from tradecrm.db.repository import OrderRepository
        from tradecrm.risk.state import RiskStateStore

        provider = _Provider()
        broker = broker or PaperBrokerAdapter(db_session, provider, initial_capital=10_000.0)
        state = RiskStateStore(db_session, broker=broker.name)
        state.set_trading_enabled(True)
        rm = RiskManager(state, total_capital_allocation_pct=0.75)
        notifier, audit, board = _Notifier(), _Audit(), set()
        runners = [
            LiveRunner(
                data_provider=provider, strategy=_Strategy(Signal.BUY), risk_manager=rm, state=state,
                broker=broker, notifier=notifier, audit_logger=audit, symbol=sym, timeframe="1h",
                sizing=SizingConfig(mode="fixed", fixed_fraction=sizing_fraction),
                order_manager=OrderManager(broker, OrderRepository(db_session)), session=db_session,
                notice_board=board,
            )
            for sym in self.SYMBOLS
        ]
        return MultiRunner(runners), broker, notifier, audit

    def test_total_exposure_cap_spans_symbols(self, db_session):
        multi, broker, _, audit = self._runners(db_session)
        multi.safe_tick()

        snap = broker.get_snapshot()
        held = {p.symbol for p in snap.positions}
        assert held == {"BTC/USDT", "ETH/USDT"}  # 5.000 + 2.500 (kalan tavan); SOL tavana takildi
        assert snap.allocated == pytest.approx(7_500, abs=10)  # komisyonlar dahil
        assert snap.cash == pytest.approx(2_500, abs=10)
        blocked = [f for e, f in audit.entries if e == "order_blocked"]
        assert [b["symbol"] for b in blocked] == ["SOL/USDT"]
        assert blocked[0]["reason"].startswith("exposure_cap_reached")
        assert "tick_error" not in audit.types()

    def test_second_tick_same_bar_no_new_orders(self, db_session):
        multi, broker, _, _ = self._runners(db_session, sizing_fraction=0.2)
        multi.safe_tick()
        first = broker.get_snapshot()
        multi.safe_tick()  # ayni mum, pozisyonlar acik -> yeni emir yok
        second = broker.get_snapshot()
        assert len(first.positions) == len(second.positions) == 3
        assert second.allocated == pytest.approx(first.allocated)

    def test_account_uncertainty_notified_once_not_per_symbol(self, db_session):
        from tradecrm.broker.paper_adapter import PaperBrokerAdapter

        class _FlakyPaper(PaperBrokerAdapter):
            fail = True

            def get_snapshot(self):
                if self.fail:
                    raise ConnectionError("bakiye alinamadi")
                return super().get_snapshot()

        broker = _FlakyPaper(db_session, _Provider(), initial_capital=10_000.0)
        multi, _, notifier, _ = self._runners(db_session, broker=broker)
        multi.safe_tick()
        multi.safe_tick()
        stopped = [m for m in notifier.messages if "yeni alimlar durdu" in m]
        assert len(stopped) == 1
        assert broker.get_open_position("BTC/USDT") is None  # belirsizken alim yok

        broker.fail = False
        multi.safe_tick()
        resumed = [m for m in notifier.messages if "tekrar kesin" in m]
        assert len(resumed) == 1
