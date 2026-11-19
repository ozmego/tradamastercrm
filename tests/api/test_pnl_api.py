from datetime import UTC, datetime, timedelta

import pytest

from tradecrm.api.services.pnl import pnl_dashboard
from tradecrm.db.models import Trade

NOW = datetime.now(UTC)


def _closed(db, symbol, pnl, days_ago, broker="paper", reason="trailing_stop", fees=1.0, entry=100.0):
    exit_price = entry * (1 + pnl / 1000)
    t = Trade(symbol=symbol, strategy_name="donchian", broker=broker, entry_time=NOW - timedelta(days=days_ago, hours=5),
              entry_price=entry, qty=10, account_balance_at_entry=10_000, exit_time=NOW - timedelta(days=days_ago),
              exit_price=exit_price, status="CLOSED", realized_pnl=pnl, fees=fees, exit_reason=reason)
    db.add(t)
    return t


def _open(db, symbol, entry=100.0, qty=10.0, broker="paper"):
    t = Trade(symbol=symbol, strategy_name="donchian", broker=broker, entry_time=NOW - timedelta(hours=3),
              entry_price=entry, qty=qty, account_balance_at_entry=10_000, entry_fees=1.0, stop_price=90.0)
    db.add(t)
    return t


@pytest.fixture()
def seeded(db_session):
    _closed(db_session, "BTC/USDT", 50.0, 2)
    _closed(db_session, "BTC/USDT", -20.0, 5, reason="stop_loss")
    _closed(db_session, "ETH/USDT", 30.0, 10)
    _closed(db_session, "ETH/USDT", -10.0, 40, reason="stop_loss")  # 30 gun disinda
    _closed(db_session, "SOL/USDT", 5.0, 3, broker="paper:mean_reversion")
    _open(db_session, "ETH/USDT")  # fiyat 100 (FakeMarket): mv 1000, maliyet 1001 -> -1
    db_session.commit()
    return db_session


class _Market:
    def __init__(self, prices):
        self.prices = prices

    def fetch_latest_price(self, symbol):
        p = self.prices[symbol]
        if p is None:
            raise ConnectionError("yok")
        return p


def test_summary_period_and_unrealized(seeded):
    d = pnl_dashboard(seeded, _Market({"ETH/USDT": 110.0}), days=30)
    s = d["summary"]
    assert s["closed"] == 4 and s["wins"] == 3 and s["losses"] == 1
    assert s["realized"] == pytest.approx(65.0)
    assert s["unrealized"] == pytest.approx(110 * 10 - 1001)  # +99
    assert s["total"] == pytest.approx(65.0 + 99.0)
    assert s["profit_factor"] == pytest.approx(85.0 / 20.0)
    assert s["win_rate"] == pytest.approx(0.75)
    assert s["avg_win"] == pytest.approx(85.0 / 3) and s["avg_loss"] == pytest.approx(-20.0)
    assert s["fees"] == pytest.approx(4.0)
    assert s["best"]["pnl"] == 50.0 and s["worst"]["pnl"] == -20.0
    assert s["open"] == 1 and s["unpriced_positions"] == 0


def test_all_time_and_account_filter(seeded):
    m = _Market({"ETH/USDT": 100.0})
    assert pnl_dashboard(seeded, m, days=0)["summary"]["closed"] == 5
    only_root = pnl_dashboard(seeded, m, days=30, account="paper")["summary"]
    assert only_root["closed"] == 3 and only_root["realized"] == pytest.approx(60.0)
    all_paper = pnl_dashboard(seeded, m, days=30, account="paper*")["summary"]
    assert all_paper["closed"] == 4


def test_cumulative_starts_at_zero_and_is_ordered(seeded):
    d = pnl_dashboard(seeded, _Market({"ETH/USDT": 100.0}), days=30)
    cum = d["cumulative"]
    assert cum[0]["cum"] == 0.0
    assert [c["pnl"] for c in cum[1:]] == [30.0, -20.0, 5.0, 50.0]  # cikis zamanina gore
    assert cum[-1]["cum"] == pytest.approx(65.0)
    times = [c["t"] for c in cum]
    assert times == sorted(times)


def test_by_symbol_includes_open_and_sorted(seeded):
    d = pnl_dashboard(seeded, _Market({"ETH/USDT": 90.0}), days=30)
    rows = {r["symbol"]: r for r in d["by_symbol"]}
    assert rows["ETH/USDT"]["realized"] == pytest.approx(30.0)
    assert rows["ETH/USDT"]["unrealized"] == pytest.approx(900 - 1001)
    assert rows["ETH/USDT"]["open"] == 1
    totals = [r["total"] for r in d["by_symbol"]]
    assert totals == sorted(totals, reverse=True)
    reasons = {r["reason"]: r for r in d["by_reason"]}
    assert reasons["stop_loss"]["count"] == 1 and reasons["trailing_stop"]["wins"] == 3


def test_unpriced_position_excluded_from_total(seeded):
    d = pnl_dashboard(seeded, _Market({"ETH/USDT": None}), days=30)
    assert d["summary"]["unrealized"] == 0.0 and d["summary"]["unpriced_positions"] == 1
    assert d["open_positions"][0]["unrealized_pnl"] is None


def test_empty(db_session):
    d = pnl_dashboard(db_session, _Market({}), days=30)
    assert d["summary"]["closed"] == 0 and d["summary"]["win_rate"] is None
    assert d["cumulative"] == [] and d["by_symbol"] == []


def test_endpoint(authed, seeded):
    res = authed.get("/api/pnl?days=30")
    assert res.status_code == 200
    data = res.json()
    assert data["summary"]["closed"] == 4
    assert data["open_positions"][0]["unrealized_pnl"] == pytest.approx(1000 - 1001)  # FakeMarket fiyat 100
    assert authed.get("/api/pnl?days=-1").status_code == 422


def test_endpoint_requires_login(client):
    assert client.get("/api/pnl").status_code == 401
