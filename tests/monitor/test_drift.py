from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from tradecrm.monitor.drift import (
    Baseline,
    DriftConfig,
    DriftGuard,
    check_drift,
    load_baseline,
    save_baseline,
)
from tradecrm.monitor.report import build_report


def _baseline(returns):
    return Baseline("p", "1d", ["BTC/USDT"], list(returns), created_at="2026-01-01T00:00:00+00:00")


# Backtest: %40 kazanan (+%10), %60 kaybeden (-%3) -> ort. +%2.2
GOOD = [0.10] * 40 + [-0.03] * 60


class TestCheckDrift:
    def test_no_baseline(self):
        assert check_drift([0.01] * 20, None).status == "no_baseline"

    def test_insufficient_trades(self):
        res = check_drift([-0.05] * 9, _baseline(GOOD))
        assert res.status == "insufficient"

    def test_ok_when_live_matches_backtest(self):
        live = ([0.10] * 4 + [-0.03] * 6) * 2
        assert check_drift(live, _baseline(GOOD)).status == "ok"

    def test_degraded_when_live_far_below(self):
        res = check_drift([-0.03] * 15, _baseline(GOOD))
        assert res.status == "degraded"
        assert res.live_mean < res.lower_bound < res.baseline_mean

    def test_normal_losing_streak_is_not_degraded(self):
        """Kazanma orani %40'ken 10 islemde 8 kayip olasi bir seridir; alarm
        her kotu seride degil, ancak backtest dagiliminin disinda calmali."""
        live = [0.10] * 2 + [-0.03] * 8  # ort. -0.4%
        res = check_drift(live, _baseline(GOOD))
        assert res.status == "ok", res.describe()

    def test_uses_only_last_window(self):
        live = [-0.03] * 50 + ([0.10] * 4 + [-0.03] * 6) * 3
        assert check_drift(live, _baseline(GOOD), DriftConfig(window=30)).status == "ok"

    def test_deterministic(self):
        a = check_drift([-0.01] * 12, _baseline(GOOD))
        b = check_drift([-0.01] * 12, _baseline(GOOD))
        assert a == b


def test_baseline_roundtrip(tmp_path):
    b = _baseline(GOOD)
    save_baseline(b, root=tmp_path)
    loaded = load_baseline("p", "1d", root=tmp_path)
    assert loaded == b
    assert load_baseline("yok", "1d", root=tmp_path) is None


# ------------------------------------------------------------ guard (gercek DB)
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


def _close_trades(db_session, returns, broker="paper", start=None):
    from tradecrm.db.repository import TradeRepository

    repo = TradeRepository(db_session)
    t0 = start or datetime.now(UTC) - timedelta(days=len(returns) + 1)
    for i, r in enumerate(returns):
        tr = repo.open_trade(
            symbol="BTC/USDT", entry_price=100.0, qty=1.0, entry_time=t0 + timedelta(days=i),
            strategy_name="s", broker=broker, account_balance_at_entry=10_000.0,
        )
        repo.close_trade(tr.id, exit_price=100.0 * (1 + r), exit_time=t0 + timedelta(days=i, hours=12))


def _enable_at(state, ts):
    state._risk_events.record_event("TRADING_ENABLED", timestamp=ts)


class TestDriftGuard:
    def _guard(self, db_session, baseline=None):
        from tradecrm.risk.state import RiskStateStore

        state = RiskStateStore(db_session, broker="paper")
        return DriftGuard(state, baseline or _baseline(GOOD), _Notifier(), _Audit()), state

    def test_disables_trading_and_notifies_once(self, db_session):
        guard, state = self._guard(db_session)
        _enable_at(state, datetime.now(UTC) - timedelta(days=30))
        _close_trades(db_session, [-0.03] * 15, start=datetime.now(UTC) - timedelta(days=20))
        guard.safe_tick()
        guard.safe_tick()
        assert state.is_trading_enabled() is False
        assert len([m for m in guard.notifier.messages if "PERFORMANS BOZULMASI" in m]) == 1

    def test_window_starts_at_last_enable(self, db_session):
        """Bozulma sonrasi enable-trading = temiz sayfa: eski kayiplar sayilmaz."""
        guard, state = self._guard(db_session)
        _close_trades(db_session, [-0.03] * 15, start=datetime.now(UTC) - timedelta(days=40))
        state.set_trading_enabled(True)
        res = guard.run_once()
        assert res.status == "insufficient"
        assert state.is_trading_enabled() is True

    def test_paper_trades_dont_leak_into_other_account(self, db_session):
        from tradecrm.risk.state import RiskStateStore

        state = RiskStateStore(db_session, broker="binance")
        _enable_at(state, datetime.now(UTC) - timedelta(days=30))
        _close_trades(db_session, [-0.03] * 15, broker="paper", start=datetime.now(UTC) - timedelta(days=20))
        guard = DriftGuard(state, _baseline(GOOD), _Notifier(), _Audit())
        assert guard.run_once().n == 0

    def test_error_does_not_raise(self):
        class _BadState:
            account = "paper"

            def last_trading_enabled_time(self):
                raise RuntimeError("db down")

        guard = DriftGuard(_BadState(), _baseline(GOOD), _Notifier(), _Audit())
        guard.safe_tick()
        assert guard.audit_logger.entries[-1][0] == "drift_check_error"

    def test_error_rolls_back_shared_session(self):
        class _Session:
            rolled_back = 0

            def rollback(self):
                self.rolled_back += 1

        class _BadState:
            account = "paper"
            session = _Session()

            def last_trading_enabled_time(self):
                raise RuntimeError("db down")

        guard = DriftGuard(_BadState(), _baseline(GOOD), _Notifier(), _Audit())
        guard.safe_tick()
        assert guard.state.session.rolled_back == 1


# --------------------------------------------------------------------- rapor
@dataclass
class _T:
    symbol: str
    entry_price: float
    exit_price: float
    realized_pnl: float
    fees: float
    exit_reason: str
    exit_time: datetime


def test_report_contents():
    now = datetime(2026, 11, 16, 8, tzinfo=UTC)
    trades = [
        _T("BTC/USDT", 100, 110, 95.0, 2.0, "trailing_stop", now - timedelta(days=2)),
        _T("ETH/USDT", 100, 97, -35.0, 2.0, "stop", now - timedelta(days=1)),
    ]
    text = build_report("paper", trades, trading_enabled=True, now=now)
    assert "Kapanan islem: 2" in text
    assert "+60.00" in text
    assert "trailing_stop=1" in text and "stop=1" in text
    assert "En kotu: ETH/USDT" in text
    assert "ACIK" in text


def test_report_empty_week():
    text = build_report("paper", [], trading_enabled=False, kill_switch_reason="gunluk zarar")
    assert "Kapanan islem: 0" in text
    assert "KAPALI" in text and "KILL-SWITCH: gunluk zarar" in text


@pytest.mark.parametrize("status", ["no_baseline", "insufficient"])
def test_drift_describe_without_numbers(status):
    from tradecrm.monitor.drift import DriftResult

    assert DriftResult(status, 3, None, None, None).describe()
