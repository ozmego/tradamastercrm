from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from tradecrm.api.services.collectors import CollectorTrigger
from tradecrm.db.models import AdminAction, CollectorRun


class _Inline:
    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, **_):
        pass


class _Fake:
    def __init__(self):
        self.last_errors = {}
        self.last_backfilled = []

    def collect(self, symbols):
        return {s: 2 for s in symbols}


def _with_trigger(ctx, db_session):
    ctx.collector_trigger = CollectorTrigger(lambda: nullcontext(db_session), ctx.collect_symbols, 45,
                                             collector_factory=lambda name, s: _Fake(), executor=_Inline())


def test_collectors_require_login(client):
    assert client.get("/api/collectors").status_code == 401


def test_list_and_detail(authed, ctx):
    [c] = authed.get("/api/collectors").json()
    assert c["name"] == "derivatives" and c["status"] == "never" and "freshness" not in c
    d = authed.get("/api/collectors/derivatives").json()
    assert len(d["freshness"]) == 3 * len(ctx.collect_symbols) and "log" in d
    assert authed.get("/api/collectors/yok").status_code == 404


def test_manual_run_records_run_and_action(authed, ctx, db_session):
    _with_trigger(ctx, db_session)
    assert authed.post("/api/collectors/derivatives/run").status_code == 202
    runs = authed.get("/api/collectors/derivatives/runs").json()
    assert runs["total"] == 1 and runs["items"][0]["trigger"] == "manual" and runs["items"][0]["status"] == "OK"
    acts = db_session.exec(select(AdminAction).where(AdminAction.action == "collector_run")).all()
    assert [a.ok for a in acts] == [True]


def test_manual_run_needs_csrf(authed, ctx, db_session):
    _with_trigger(ctx, db_session)
    del authed.headers["X-CSRF-Token"]
    assert authed.post("/api/collectors/derivatives/run").status_code == 403


def test_manual_run_conflict_when_running(authed, ctx, db_session):
    _with_trigger(ctx, db_session)
    db_session.add(CollectorRun(collector="derivatives", started_at=datetime.now(UTC) - timedelta(minutes=1)))
    db_session.commit()
    assert authed.post("/api/collectors/derivatives/run").status_code == 409


def test_runs_filter_and_validation(authed, db_session):
    now = datetime.now(UTC)
    for i, st in enumerate(["OK", "FAILED", "OK"]):
        db_session.add(CollectorRun(collector="derivatives", status=st, started_at=now - timedelta(hours=i + 1)))
    db_session.commit()
    assert authed.get("/api/collectors/derivatives/runs?status=OK").json()["total"] == 2
    assert authed.get("/api/collectors/derivatives/runs?status=xx").status_code == 400
    page = authed.get("/api/collectors/derivatives/runs?limit=1&offset=1").json()
    assert page["total"] == 3 and len(page["items"]) == 1


def test_overview_includes_collector_alert(authed, ctx, db_session):
    _with_trigger(ctx, db_session)
    authed.post("/api/collectors/derivatives/run")  # sahte toplayici veri yazmaz -> semboller "missing"
    data = authed.get("/api/overview").json()
    assert data["collectors"]["derivatives"] == "failing"
    assert any(a.get("source") == "collector:derivatives" for a in data["alerts"])
