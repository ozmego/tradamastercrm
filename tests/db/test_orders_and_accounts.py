"""DB seviyesi: emir tekilligi (eszamanli dahil) ve hesap bazli risk bayraklari."""

import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlmodel import Session

from tradecrm.db.models import Order, RiskEvent
from tradecrm.db.repository import (
    DuplicateOrderError,
    OrderRepository,
    RiskEventRepository,
    TradeRepository,
    make_client_order_id,
)
from tradecrm.risk.state import RiskStateStore

BAR = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


class TestOrderUniqueness:
    def test_second_reserve_for_same_intent_rejected(self, db_session):
        repo = OrderRepository(db_session)
        first = repo.reserve("paper", "BTC/USDT", "BUY", BAR)
        assert first.status == "NEW"
        with pytest.raises(DuplicateOrderError):
            repo.reserve("paper", "BTC/USDT", "BUY", BAR)
        # Farkli yon / mum / hesap serbest.
        repo.reserve("paper", "BTC/USDT", "SELL", BAR)
        repo.reserve("paper", "BTC/USDT", "BUY", datetime(2026, 3, 1, 13, 0, tzinfo=UTC))
        repo.reserve("paper:x", "BTC/USDT", "BUY", BAR)
        assert len(repo.unresolved("paper")) == 3

    def test_client_order_id_deterministic_and_binance_compatible(self):
        a = make_client_order_id("paper", "BTC/USDT", "BUY", BAR)
        assert a == make_client_order_id("paper", "BTC/USDT", "BUY", BAR)
        assert a != make_client_order_id("binance", "BTC/USDT", "BUY", BAR)
        assert len(a) <= 36
        assert all(c.isalnum() or c in "-_" for c in a)

    def test_update_and_resolution(self, db_session):
        repo = OrderRepository(db_session)
        order = repo.reserve("paper", "BTC/USDT", "BUY", BAR)
        repo.update(order, status="FILLED", filled_qty=1.0)
        assert repo.get(order.client_order_id).status == "FILLED"
        assert repo.unresolved("paper") == []


def test_concurrent_reserve_only_one_wins(_test_engine):
    """Iki ayri baglanti (iki runner sureci gibi) ayni anda ayni niyeti
    kaydetmeye calisir: veritabani kisiti sayesinde tam olarak biri kazanir."""
    bar = datetime(2031, 1, 1, tzinfo=UTC)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker():
        with Session(_test_engine) as session:
            barrier.wait()
            try:
                OrderRepository(session).reserve("paper", "CONC/USDT", "BUY", bar)
                results.append("ok")
            except DuplicateOrderError:
                results.append("dup")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert sorted(results) == ["dup", "ok"]
    finally:
        with Session(_test_engine) as session:
            session.exec(delete(Order).where(Order.symbol == "CONC/USDT"))
            session.commit()


class TestAccountScopedRiskFlags:
    def test_paper_and_live_flags_are_separate(self, db_session):
        paper = RiskStateStore(db_session, broker="paper")
        live = RiskStateStore(db_session, broker="binance")
        paper.set_trading_enabled(True)
        paper.trip_kill_switch("paper zarar")
        assert live.is_trading_enabled() is False
        assert live.is_kill_switch_tripped() is False

    def test_sub_account_inherits_root(self, db_session):
        root = RiskStateStore(db_session, broker="paper")
        sub = RiskStateStore(db_session, broker="paper:donchian")
        assert sub.is_trading_enabled() is False
        root.set_trading_enabled(True)
        assert sub.is_trading_enabled() is True
        sub.set_trading_enabled(False)  # alt hesap kendini kapatabilir
        assert sub.is_trading_enabled() is False
        assert root.is_trading_enabled() is True
        sub.set_trading_enabled(True)
        root.set_trading_enabled(False)  # kok kapaninca hepsi durur
        assert sub.is_trading_enabled() is False

    def test_kill_switch_root_affects_sub_but_not_vice_versa(self, db_session):
        root = RiskStateStore(db_session, broker="paper")
        sub = RiskStateStore(db_session, broker="paper:mr")
        other = RiskStateStore(db_session, broker="paper:trend")
        sub.trip_kill_switch("mr zarar")
        assert sub.is_kill_switch_tripped()
        assert not root.is_kill_switch_tripped()
        assert not other.is_kill_switch_tripped()
        root.trip_kill_switch("toplam zarar")
        assert other.is_kill_switch_tripped()
        assert other.kill_switch_reason.startswith("[paper]")

    def test_daily_pnl_separated_by_account(self, db_session):
        repo = TradeRepository(db_session)
        for broker, exit_price in (("paper", 110.0), ("binance", 90.0)):
            t = repo.open_trade(symbol="BTC/USDT", strategy_name="s", broker=broker, entry_time=BAR,
                                entry_price=100.0, qty=1.0, account_balance_at_entry=100.0)
            repo.close_trade(t.id, exit_time=BAR, exit_price=exit_price)
        assert RiskStateStore(db_session, broker="paper").get_realized_pnl_pct(BAR.date()) == pytest.approx(0.10)
        assert RiskStateStore(db_session, broker="binance").get_realized_pnl_pct(BAR.date()) == pytest.approx(-0.10)

    def test_legacy_events_default_to_paper(self, db_session):
        db_session.add(RiskEvent(event_type="TRADING_ENABLED"))  # account belirtilmeden (eski satir gibi)
        db_session.commit()
        assert RiskEventRepository(db_session, account="paper").is_trading_enabled() is True
        assert RiskEventRepository(db_session, account="binance").is_trading_enabled() is False


def test_paper_order_flow_end_to_end(db_session):
    """Gercek DB + gercek paper broker + gercek OrderManager: emir kaydi,
    fill, trade baglantisi ve tekrar engeli."""
    from tradecrm.broker.order_manager import OrderManager
    from tradecrm.broker.paper_adapter import PaperBrokerAdapter
    from tradecrm.strategy.base import Signal

    class _Price:
        def fetch_latest_price(self, symbol):
            return 100.0

    broker = PaperBrokerAdapter(db_session, _Price())
    om = OrderManager(broker, OrderRepository(db_session))
    first = om.execute("BTC/USDT", Signal.BUY, BAR, "s", notional=1_000.0)
    assert first.status == "FILLED"
    order = OrderRepository(db_session).get(first.client_order_id)
    assert order.trade_id == first.result.trade_id
    assert om.execute("BTC/USDT", Signal.BUY, BAR, "s", notional=1_000.0).status == "DUPLICATE"
    assert om.reconcile().uncertain is False
