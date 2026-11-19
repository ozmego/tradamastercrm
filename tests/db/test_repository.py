from datetime import UTC, date, datetime

import pytest

from tradecrm.db.repository import RiskEventRepository, TradeRepository


class TestTradeRepositoryOpenClose:
    def test_open_trade_sets_open_status(self, db_session):
        repo = TradeRepository(db_session)
        trade = repo.open_trade(
            symbol="BTC/USDT",
            strategy_name="ema_rsi_crossover",
            broker="paper",
            entry_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            entry_price=100.0,
            qty=1.0,
            account_balance_at_entry=1000.0,
        )
        assert trade.id is not None
        assert trade.status == "OPEN"
        assert trade.realized_pnl is None

    def test_close_trade_computes_pnl_and_pct(self, db_session):
        repo = TradeRepository(db_session)
        trade = repo.open_trade(
            symbol="BTC/USDT",
            strategy_name="ema_rsi_crossover",
            broker="paper",
            entry_time=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            entry_price=100.0,
            qty=2.0,
            account_balance_at_entry=1000.0,
        )
        closed = repo.close_trade(
            trade.id,
            exit_time=datetime(2026, 1, 1, 14, 0, tzinfo=UTC),
            exit_price=110.0,
            fees=1.0,
        )
        # gross = (110-100)*2 = 20; fees=1 -> realized_pnl=19; pct = 19/1000
        assert closed.status == "CLOSED"
        assert closed.realized_pnl == pytest.approx(19.0)
        assert closed.realized_pnl_pct == pytest.approx(0.019)

    def test_close_trade_unknown_id_raises(self, db_session):
        repo = TradeRepository(db_session)
        with pytest.raises(KeyError):
            repo.close_trade(999, exit_time=datetime.now(UTC), exit_price=1.0)

    def test_close_already_closed_trade_raises(self, db_session):
        repo = TradeRepository(db_session)
        trade = repo.open_trade(
            symbol="BTC/USDT",
            strategy_name="s",
            broker="paper",
            entry_time=datetime(2026, 1, 1, tzinfo=UTC),
            entry_price=100.0,
            qty=1.0,
            account_balance_at_entry=1000.0,
        )
        repo.close_trade(trade.id, exit_time=datetime(2026, 1, 1, 1, tzinfo=UTC), exit_price=105.0)
        with pytest.raises(ValueError):
            repo.close_trade(trade.id, exit_time=datetime(2026, 1, 1, 2, tzinfo=UTC), exit_price=106.0)


class TestOpenPositionCount:
    def test_counts_only_open_trades(self, db_session):
        repo = TradeRepository(db_session)
        t1 = repo.open_trade("BTC/USDT", "s", "paper", datetime(2026, 1, 1, tzinfo=UTC), 100.0, 1.0, 1000.0)
        repo.open_trade("ETH/USDT", "s", "paper", datetime(2026, 1, 1, tzinfo=UTC), 50.0, 1.0, 1000.0)
        assert repo.get_open_position_count() == 2

        repo.close_trade(t1.id, exit_time=datetime(2026, 1, 1, 1, tzinfo=UTC), exit_price=101.0)
        assert repo.get_open_position_count() == 1

    def test_filters_by_symbol(self, db_session):
        repo = TradeRepository(db_session)
        repo.open_trade("BTC/USDT", "s", "paper", datetime(2026, 1, 1, tzinfo=UTC), 100.0, 1.0, 1000.0)
        repo.open_trade("ETH/USDT", "s", "paper", datetime(2026, 1, 1, tzinfo=UTC), 50.0, 1.0, 1000.0)
        assert len(repo.get_open_trades(symbol="BTC/USDT")) == 1


class TestDailyAggregation:
    def test_realized_pnl_pct_for_date_sums_same_day_trades(self, db_session):
        repo = TradeRepository(db_session)
        t1 = repo.open_trade("BTC/USDT", "s", "paper", datetime(2026, 1, 1, 8, tzinfo=UTC), 100.0, 1.0, 1000.0)
        t2 = repo.open_trade("BTC/USDT", "s", "paper", datetime(2026, 1, 1, 9, tzinfo=UTC), 100.0, 1.0, 1000.0)
        repo.close_trade(t1.id, exit_time=datetime(2026, 1, 1, 10, tzinfo=UTC), exit_price=110.0)  # +0.01
        repo.close_trade(t2.id, exit_time=datetime(2026, 1, 1, 11, tzinfo=UTC), exit_price=90.0)  # -0.01

        pct = repo.get_realized_pnl_pct_for_date(date(2026, 1, 1))
        assert pct == pytest.approx(0.0, abs=1e-9)

    def test_midnight_utc_boundary_splits_trades_correctly(self, db_session):
        """Giris ayni gun, cikis ertesi gune sarkan bir islem, cikis
        tarihine (kapanis gunune) gore raporlanmali."""
        repo = TradeRepository(db_session)
        trade = repo.open_trade(
            "BTC/USDT", "s", "paper", datetime(2026, 1, 1, 23, 0, tzinfo=UTC), 100.0, 1.0, 1000.0
        )
        repo.close_trade(trade.id, exit_time=datetime(2026, 1, 2, 1, 0, tzinfo=UTC), exit_price=110.0)

        assert repo.get_realized_pnl_pct_for_date(date(2026, 1, 1)) == pytest.approx(0.0)
        assert repo.get_realized_pnl_pct_for_date(date(2026, 1, 2)) == pytest.approx(0.01)

    def test_get_daily_report_rows(self, db_session):
        repo = TradeRepository(db_session)
        t1 = repo.open_trade("BTC/USDT", "s", "paper", datetime(2026, 1, 1, 8, tzinfo=UTC), 100.0, 1.0, 1000.0)
        t2 = repo.open_trade("BTC/USDT", "s", "paper", datetime(2026, 1, 2, 8, tzinfo=UTC), 100.0, 1.0, 1000.0)
        repo.close_trade(t1.id, exit_time=datetime(2026, 1, 1, 10, tzinfo=UTC), exit_price=110.0)
        repo.close_trade(t2.id, exit_time=datetime(2026, 1, 2, 10, tzinfo=UTC), exit_price=90.0)

        rows = repo.get_daily_report(date(2026, 1, 1), date(2026, 1, 2))
        assert len(rows) == 2
        assert rows[0].date == date(2026, 1, 1)
        assert rows[0].trade_count == 1
        assert rows[0].win_count == 1
        assert rows[0].loss_count == 0
        assert rows[1].date == date(2026, 1, 2)
        assert rows[1].loss_count == 1


class TestRiskEventRepository:
    def test_trading_enabled_defaults_false(self, db_session):
        repo = RiskEventRepository(db_session)
        assert repo.is_trading_enabled() is False

    def test_trading_enabled_reflects_latest_event(self, db_session):
        repo = RiskEventRepository(db_session)
        repo.record_event("TRADING_ENABLED")
        assert repo.is_trading_enabled() is True
        repo.record_event("TRADING_DISABLED")
        assert repo.is_trading_enabled() is False

    def test_kill_switch_trip_and_reset(self, db_session):
        repo = RiskEventRepository(db_session)
        assert repo.is_kill_switch_tripped() is False
        repo.record_event("KILL_SWITCH_TRIP", reason="daily loss")
        assert repo.is_kill_switch_tripped() is True
        assert repo.get_kill_switch_reason() == "daily loss"
        repo.record_event("KILL_SWITCH_RESET")
        assert repo.is_kill_switch_tripped() is False
        assert repo.get_kill_switch_reason() == ""

    def test_first_order_confirmation(self, db_session):
        repo = RiskEventRepository(db_session)
        assert repo.is_first_order_confirmed() is False
        repo.record_event("FIRST_ORDER_CONFIRMED")
        assert repo.is_first_order_confirmed() is True
