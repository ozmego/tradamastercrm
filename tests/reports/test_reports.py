import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import select

from tradecrm.db.models import (
    AdminAction,
    CollectorRun,
    EquitySnapshot,
    Report,
    RiskEvent,
    Trade,
)
from tradecrm.reports.builder import ReportParamsError, build_report, resolve_period
from tradecrm.reports.render import to_markdown, to_text, trades_csv
from tradecrm.reports.store import create_report

D = lambda *a: datetime(*a, tzinfo=UTC)


class TestPeriods:
    TODAY = date(2026, 11, 18)  # carsamba

    def test_day_defaults_to_yesterday(self):
        p = resolve_period("day", today=self.TODAY)
        assert p.start == D(2026, 11, 17) and p.end == D(2026, 11, 18)

    def test_week_is_previous_monday_to_sunday(self):
        p = resolve_period("week", today=self.TODAY)
        assert p.start == D(2026, 11, 9) and p.end == D(2026, 11, 16)
        p2 = resolve_period("week", start=date(2026, 11, 18), today=self.TODAY)  # icinde bulunulan hafta
        assert p2.start == D(2026, 11, 16)

    def test_month_previous_and_specific(self):
        p = resolve_period("month", today=self.TODAY)
        assert p.start == D(2026, 10, 1) and p.end == D(2026, 11, 1)
        feb = resolve_period("month", start=date(2028, 2, 10), today=date(2028, 3, 5))
        assert feb.end == D(2028, 3, 1)  # artik yil

    def test_custom_inclusive_end(self):
        p = resolve_period("custom", date(2026, 11, 1), date(2026, 11, 3), today=self.TODAY)
        assert p.start == D(2026, 11, 1) and p.end == D(2026, 11, 4)

    @pytest.mark.parametrize("args", [
        ("custom", None, None), ("custom", date(2026, 11, 5), date(2026, 11, 1)), ("year", None, None),
        ("custom", date(2026, 12, 1), date(2026, 12, 2)), ("custom", date(2010, 1, 1), date(2026, 1, 1)),
    ])
    def test_invalid(self, args):
        with pytest.raises(ReportParamsError):
            resolve_period(*args, today=self.TODAY)


def _trade(db, symbol, pnl, exit_at, broker="paper", reason="trailing_stop", entry_at=None):
    t = Trade(symbol=symbol, strategy_name="d", broker=broker, entry_time=entry_at or exit_at - timedelta(hours=10),
              entry_price=100.0, qty=1.0, account_balance_at_entry=1e4, exit_time=exit_at,
              exit_price=100.0 + pnl, status="CLOSED", realized_pnl=pnl, fees=0.2, exit_reason=reason)
    db.add(t)
    return t


@pytest.fixture()
def week(db_session):
    db = db_session
    # 9-15 Kasim haftasi
    _trade(db, "BTC/USDT", 30.0, D(2026, 11, 10, 12))
    _trade(db, "BTC/USDT", -10.0, D(2026, 11, 10, 18), reason="stop_loss")
    _trade(db, "ETH/USDT", -5.0, D(2026, 11, 12, 9), reason="stop_loss")
    _trade(db, "ETH/USDT", 20.0, D(2026, 11, 15, 23, 59), broker="paper:mr")
    _trade(db, "SOL/USDT", 99.0, D(2026, 11, 16, 0, 0))  # sonraki hafta (haric)
    _trade(db, "SOL/USDT", 7.0, D(2026, 11, 8, 23, 59))  # onceki hafta
    # donem icinde acilip donem sonrasi kapanan + hala acik
    _trade(db, "XRP/USDT", 1.0, D(2026, 11, 17), entry_at=D(2026, 11, 14))
    db.add(Trade(symbol="DOGE/USDT", strategy_name="d", broker="paper", entry_time=D(2026, 11, 13), entry_price=1,
                 qty=1, account_balance_at_entry=1e4))
    for i, eq in enumerate([10_000, 10_100, 9_900, 10_050]):
        db.add(EquitySnapshot(account="paper", ts=D(2026, 11, 10 + i), cash=eq, allocated=0, market_value=0,
                              equity=eq, open_positions=0))
    db.add(RiskEvent(event_type="TRADING_ENABLED", account="paper", timestamp=D(2026, 11, 9, 8), reason="x"))
    db.add(RiskEvent(event_type="KILL_SWITCH_TRIP", account="binance", timestamp=D(2026, 11, 11), reason="y"))
    db.add(CollectorRun(collector="derivatives", status="OK", started_at=D(2026, 11, 10), rows_added=30))
    db.add(CollectorRun(collector="derivatives", status="FAILED", started_at=D(2026, 11, 11)))
    db.add(AdminAction(username="ayse", action="live_start", ts=D(2026, 11, 10), detail=json.dumps({"a": 1})))
    db.commit()
    return db


def _period():
    return resolve_period("week", start=date(2026, 11, 10), today=date(2026, 11, 18))


def test_summary_counts_only_period_trades(week):
    r = build_report(week, _period(), now=D(2026, 11, 18))
    s = r["summary"]
    assert s["closed"] == 4 and s["realized"] == pytest.approx(35.0)
    assert s["wins"] == 2 and s["losses"] == 2 and s["win_rate"] == 0.5
    assert s["profit_factor"] == pytest.approx(50 / 15)
    assert s["longest_losing_streak"] == 2  # -10, -5 ardisik (cikis sirasina gore)
    assert s["fees"] == pytest.approx(0.8)
    # donem icinde ACILAN: 4 kapanan + XRP + DOGE + sonraki haftada kapanan SOL (15 Kas 14:00'te acildi)
    assert s["opened"] == 7
    # donem sonunda acik: XRP (sonra kapandi) + DOGE (acik) + SOL (tam bitis aninda, donem disinda kapandi)
    assert s["open_at_end"] == 3
    assert s["best"]["symbol"] == "BTC/USDT" and s["worst"]["pnl"] == -10.0
    assert r["period"]["complete"] is True


def test_daily_includes_empty_days_and_cumulative(week):
    r = build_report(week, _period(), now=D(2026, 11, 18))
    assert r["daily"][0]["date"] == "2026-11-09" and len(r["daily"]) == 7
    by = {d["date"]: d for d in r["daily"]}
    assert by["2026-11-10"]["pnl"] == pytest.approx(20.0) and by["2026-11-10"]["trades"] == 2
    assert by["2026-11-11"]["trades"] == 0
    assert r["daily"][-1]["cum"] == pytest.approx(35.0)


def test_equity_return_and_drawdown(week):
    [e] = build_report(week, _period(), now=D(2026, 11, 18))["equity"]
    assert e["start"] == 10_000 and e["end"] == 10_050
    assert e["return_pct"] == pytest.approx(0.005)
    assert e["max_drawdown"] == pytest.approx(1 - 9_900 / 10_100)


def test_account_filter_and_root_risk_events(week):
    r = build_report(week, _period(), account="paper:mr", now=D(2026, 11, 18))
    assert r["summary"]["closed"] == 1 and r["summary"]["realized"] == 20.0
    assert [e["type"] for e in r["risk_events"]] == ["TRADING_ENABLED"]  # kok hesap olayi dahil, binance haric
    all_paper = build_report(week, _period(), account="paper*", now=D(2026, 11, 18))
    assert all_paper["summary"]["closed"] == 4


def test_collector_admin_and_reasons(week):
    r = build_report(week, _period(), now=D(2026, 11, 18))
    assert r["collector"] == {"runs": 2, "ok": 1, "partial": 0, "failed": 1, "rows_added": 30, "rows_purged": 0}
    assert r["admin_actions"][0]["action"] == "live_start" and r["admin_actions"][0]["detail"] == {"a": 1}
    reasons = {x["reason"]: x for x in r["by_reason"]}
    assert reasons["stop_loss"]["count"] == 2 and reasons["stop_loss"]["pnl"] == pytest.approx(-15.0)
    assert [x["symbol"] for x in r["by_symbol"]] == ["BTC/USDT", "ETH/USDT"]


def test_incomplete_period_flag(week):
    p = resolve_period("week", start=date(2026, 11, 18), today=date(2026, 11, 18))
    assert build_report(week, p, now=D(2026, 11, 18, 12))["period"]["complete"] is False


def test_renderers(week):
    r = build_report(week, _period(), now=D(2026, 11, 18))
    md = to_markdown(r)
    assert md.startswith("# Haftalik rapor 09.11 - 15.11.2026")
    assert "| Net gerceklesen K/Z | +35.00 USDT |" in md and "## Risk olaylari" in md
    text = to_text(r)
    assert "Net K/Z: +35.00 USDT | 4 islem (2K/2Z)" in text
    csv_lines = trades_csv(r).strip().splitlines()
    assert csv_lines[0].startswith("id,account,symbol") and len(csv_lines) == 5


def test_empty_period_renders(db_session):
    r = build_report(db_session, _period(), now=D(2026, 11, 18))
    assert r["summary"]["closed"] == 0 and r["equity"] == [] and len(r["daily"]) == 7
    assert "Kapanan islem | 0" in to_markdown(r)
    assert to_text(r)


def test_create_report_stores_frozen_snapshot(week):
    row = create_report(week, "week", start=date(2026, 11, 10), created_by="ayse", now=D(2026, 11, 18))
    assert row.id and row.title.startswith("Haftalik rapor") and row.created_by == "ayse"
    # sonradan eklenen islem kayitli raporu degistirmez
    _trade(week, "ADA/USDT", 500.0, D(2026, 11, 11))
    week.commit()
    stored = json.loads(week.exec(select(Report).where(Report.id == row.id)).one().data)
    assert stored["summary"]["realized"] == pytest.approx(35.0)
