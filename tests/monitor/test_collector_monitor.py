import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from tradecrm.data.collectors.derivatives import upsert_metrics
from tradecrm.db.models import CollectorRun
from tradecrm.monitor.collectors import (
    COLLECTORS,
    collector_health,
    health_alerts,
    mark_interrupted,
    next_run_at,
    run_cycle,
)

SPEC = COLLECTORS["derivatives"]
SYMS = ["BTC/USDT", "ETH/USDT"]


class FakeCollector:
    def __init__(self, result=None, errors=None, boom=None):
        self.result = result if result is not None else {s: 3 for s in SYMS}
        self.last_errors = errors or {}
        self.last_backfilled = ["BTC/USDT"]
        self.boom = boom

    def collect(self, symbols):
        if self.boom:
            raise self.boom
        return dict(self.result)


def _metrics(sym, last, hours=5, skip=()):
    rows = []
    for h in range(hours):
        if h in skip:
            continue
        ts = last - timedelta(hours=h)
        rows += [{"symbol": sym, "metric": m, "period": "1h", "ts": ts, "value": 1.0} for m in SPEC.metrics]
    return rows


def _health(session, now, running=True, symbols=SYMS):
    return collector_health(session, SPEC, process_running=running, process_pid=1 if running else None,
                            process_started_at=None, symbols=symbols, retention_days=45, now=now)


def _fresh(session, now):
    last = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    for s in SYMS:
        upsert_metrics(session, _metrics(s, last))


def test_run_cycle_records_ok_run(db_session):
    run = run_cycle(db_session, "derivatives", FakeCollector(), SYMS, 45, trigger="cli")
    assert run.status == "OK" and run.rows_added == 6 and run.symbols_failed == 0
    assert run.trigger == "cli" and run.finished_at is not None and run.duration_ms is not None
    assert json.loads(run.backfilled) == ["BTC/USDT"]


def test_run_cycle_partial_and_failed(db_session):
    run = run_cycle(db_session, "derivatives",
                    FakeCollector({"BTC/USDT": 3, "ETH/USDT": -1}, {"ETH/USDT": "NetworkError: x"}), SYMS, 45)
    assert run.status == "PARTIAL" and run.symbols_failed == 1
    assert json.loads(run.errors) == {"ETH/USDT": "NetworkError: x"}
    run = run_cycle(db_session, "derivatives", FakeCollector({"BTC/USDT": -1, "ETH/USDT": -1}), SYMS, 45)
    assert run.status == "FAILED"


def test_run_cycle_never_raises(db_session):
    run = run_cycle(db_session, "derivatives", FakeCollector(boom=RuntimeError("db gitti")), SYMS, 45)
    assert run.status == "FAILED" and "db gitti" in run.error and run.finished_at is not None


def test_stale_running_marked_interrupted(db_session):
    now = datetime.now(UTC)
    db_session.add(CollectorRun(collector="derivatives", started_at=now - timedelta(hours=2)))
    db_session.add(CollectorRun(collector="derivatives", started_at=now - timedelta(minutes=1)))
    db_session.commit()
    assert mark_interrupted(db_session, "derivatives", now) == 1
    statuses = sorted(r.status for r in db_session.exec(select(CollectorRun)))
    assert statuses == ["INTERRUPTED", "RUNNING"]


def test_old_runs_purged_with_retention(db_session):
    db_session.add(CollectorRun(collector="derivatives", status="OK",
                                started_at=datetime.now(UTC) - timedelta(days=46)))
    db_session.commit()
    run_cycle(db_session, "derivatives", FakeCollector(), SYMS, 45)
    assert len(list(db_session.exec(select(CollectorRun)))) == 1


def test_next_run_at():
    now = datetime(2026, 11, 18, 10, 1, tzinfo=UTC)
    assert next_run_at(SPEC, now) == datetime(2026, 11, 18, 10, 2, tzinfo=UTC)
    assert next_run_at(SPEC, now + timedelta(minutes=5)) == datetime(2026, 11, 18, 11, 2, tzinfo=UTC)


def test_health_never(db_session):
    h = _health(db_session, datetime.now(UTC))
    assert h["status"] == "never"
    assert health_alerts(h) == []


def test_health_healthy_with_fresh_data(db_session):
    now = datetime.now(UTC)
    _fresh(db_session, now)
    run_cycle(db_session, "derivatives", FakeCollector(), SYMS, 45)
    h = _health(db_session, now)
    assert h["status"] == "healthy", h["issues"]
    assert h["next_run_at"] is not None
    f = next(x for x in h["freshness"] if x["symbol"] == "BTC/USDT" and x["metric"] == "open_interest")
    assert f["rows"] == 5 and f["lag_periods"] == 0 and f["missing_periods"] == 0 and f["status"] == "ok"


def test_health_detects_stale_missing_and_gaps(db_session):
    now = datetime(2026, 11, 18, 10, 30, tzinfo=UTC)
    upsert_metrics(db_session, _metrics("BTC/USDT", datetime(2026, 11, 18, 4, tzinfo=UTC), hours=6, skip=(2, 3)))
    h = _health(db_session, now)
    assert h["status"] == "failing"  # listedeki her sembol bayat ya da yok
    assert h["data"]["stale_symbols"] == ["BTC/USDT"] and h["data"]["missing_symbols"] == ["ETH/USDT"]
    f = next(x for x in h["freshness"] if x["symbol"] == "BTC/USDT" and x["metric"] == "open_interest")
    assert f["lag_periods"] == 5 and f["missing_periods"] == 2
    [alert] = health_alerts(h)
    assert alert["level"] == "critical"


def test_health_stopped_and_failing(db_session):
    now = datetime.now(UTC)
    _fresh(db_session, now)
    run_cycle(db_session, "derivatives", FakeCollector(), SYMS, 45)
    assert _health(db_session, now, running=False)["status"] == "stopped"
    for _ in range(3):
        run_cycle(db_session, "derivatives", FakeCollector({"BTC/USDT": -1, "ETH/USDT": -1}), SYMS, 45)
    h = _health(db_session, datetime.now(UTC))
    assert h["status"] == "failing" and h["consecutive_failures"] == 3
    assert h["stats_24h"]["runs"] == 4 and h["stats_24h"]["failed"] == 3


def test_health_partial_is_degraded(db_session):
    now = datetime.now(UTC)
    _fresh(db_session, now)
    run_cycle(db_session, "derivatives", FakeCollector({"BTC/USDT": 3, "ETH/USDT": -1}, {"ETH/USDT": "x"}), SYMS, 45)
    h = _health(db_session, datetime.now(UTC))
    assert h["status"] == "degraded"
    assert any("ETH/USDT" in i for i in h["issues"])


@pytest.mark.parametrize("status,level", [("failing", "critical"), ("stopped", "warning"),
                                          ("degraded", "warning"), ("healthy", None)])
def test_alert_levels(status, level):
    alerts = health_alerts({"name": "derivatives", "status": status, "issues": ["x"]})
    assert (alerts[0]["level"] if alerts else None) == level
