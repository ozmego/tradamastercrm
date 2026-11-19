from datetime import UTC, datetime, timedelta

import pytest

from tradecrm.db.models import Trade
from tradecrm.db.repository import AccountRepository


def _account(db):
    AccountRepository(db).register("paper", 10_000, mode="paper", profile="donchian_fng", timeframe="1d")


def _closed(db, pct):
    now = datetime.now(UTC)
    db.add(Trade(symbol="BTC/USDT", strategy_name="d", broker="paper", entry_time=now - timedelta(hours=2),
                 entry_price=100, qty=1, account_balance_at_entry=10_000, exit_time=now - timedelta(minutes=5),
                 exit_price=100, status="CLOSED", realized_pnl=pct * 10_000, realized_pnl_pct=pct, fees=0))
    db.commit()


def test_progress_fields_and_no_block_below_target(authed, db_session):
    _account(db_session)
    _closed(db_session, 0.02)
    acc = authed.get("/api/accounts/paper").json()
    assert acc["pnl_today"]["target_pct"] == pytest.approx(0.05)
    assert acc["pnl_today"]["total_pct"] == pytest.approx(0.02)
    assert not any("gunluk hedef" in b for b in acc["flags"]["blockers"])


def test_target_reached_shows_blocker(authed, db_session):
    _account(db_session)
    _closed(db_session, 0.06)
    acc = authed.get("/api/accounts/paper").json()
    assert any(b.startswith("gunluk hedef (+5%) dolu") for b in acc["flags"]["blockers"])
    assert acc["flags"]["buy_allowed"] is False
