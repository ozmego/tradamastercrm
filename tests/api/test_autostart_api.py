from datetime import UTC, datetime

import pytest
from sqlmodel import select

from tradecrm.api.services.autostart import AutostartService
from tradecrm.api.services.backtests import BacktestQueue
from tradecrm.api.services.live import LiveStartError, prepare_live_start
from tradecrm.db.models import AdminAction, BacktestRun
from tradecrm.db.repository import AccountRepository
from tradecrm.runner.autostart import TASKS, AutostartStatus

LIVE_BODY = {"profile": "donchian_fng", "symbols": ["BTC/USDT", "ETH/USDT"], "timeframe": "1d",
             "initial_capital": 10_000}


class FakeBackend:
    TASKS = TASKS

    def __init__(self, ok=True):
        self.installed: set[str] = set()
        self.ok = ok
        self.status_calls = 0

    def supported(self):
        return True

    def status_all(self):
        self.status_calls += 1
        return {k: AutostartStatus(installed=k in self.installed, state="Ready" if k in self.installed else None)
                for k in TASKS}

    def enable(self, working_dir, key):
        if not self.ok:
            raise RuntimeError("Access is denied")
        self.installed.add(key)
        return AutostartStatus(installed=True, state="Ready", command=f"pythonw {TASKS[key].arguments}")

    def disable(self, key):
        had = key in self.installed
        self.installed.discard(key)
        return had


def _svc(ctx, backend=None, tmp_path=None):
    backend = backend or FakeBackend()
    ctx.autostart = AutostartService(tmp_path, impl=backend)
    return backend


def _actions(db, action):
    return db.exec(select(AdminAction).where(AdminAction.action == action)).all()


class TestAutostartApi:
    def test_status_lists_all_tasks(self, authed, ctx, tmp_path):
        _svc(ctx, tmp_path=tmp_path)
        data = authed.get("/api/autostart").json()
        assert data["supported"] is True and set(data["tasks"]) == {"collector", "live", "admin", "live_kucoin"}
        assert data["tasks"]["admin"]["task_name"] == "TradeCRM Admin Paneli"
        assert data["tasks"]["collector"]["installed"] is False

    def test_status_is_cached(self, authed, ctx, tmp_path):
        backend = _svc(ctx, tmp_path=tmp_path)
        authed.get("/api/autostart")
        authed.get("/api/autostart")
        assert backend.status_calls == 1

    def test_enable_disable_collector_records_action(self, authed, ctx, db_session, tmp_path):
        _svc(ctx, tmp_path=tmp_path)
        assert authed.post("/api/autostart/collector/enable").status_code == 200
        assert authed.get("/api/autostart").json()["tasks"]["collector"]["installed"] is True  # onbellek silindi
        assert authed.post("/api/autostart/collector/disable").json()["result"] == {"removed": True}
        assert [a.ok for a in _actions(db_session, "autostart_enable")] == [True]
        assert [a.ok for a in _actions(db_session, "autostart_disable")] == [True]

    def test_requires_csrf(self, authed, ctx, tmp_path):
        _svc(ctx, tmp_path=tmp_path)
        del authed.headers["X-CSRF-Token"]
        assert authed.post("/api/autostart/collector/enable").status_code == 403

    def test_unknown_task_or_action(self, authed, ctx, tmp_path):
        _svc(ctx, tmp_path=tmp_path)
        assert authed.post("/api/autostart/nope/enable").status_code == 404
        assert authed.post("/api/autostart/collector/restart").status_code == 404

    def test_live_autostart_needs_previous_start(self, authed, ctx, tmp_path):
        _svc(ctx, tmp_path=tmp_path)
        assert authed.post("/api/autostart/live/enable").status_code == 409
        assert authed.post("/api/processes/live/start", json=LIVE_BODY).status_code == 200
        assert authed.post("/api/autostart/live/enable").status_code == 200

    def test_scheduler_error_is_502_and_logged(self, authed, ctx, db_session, tmp_path):
        _svc(ctx, FakeBackend(ok=False), tmp_path=tmp_path)
        res = authed.post("/api/autostart/admin/enable")
        assert res.status_code == 502 and "Access is denied" in res.json()["detail"]
        assert [a.ok for a in _actions(db_session, "autostart_enable")] == [False]

    def test_unsupported_platform(self, authed, ctx, tmp_path):
        backend = _svc(ctx, tmp_path=tmp_path)
        backend.supported = lambda: False
        assert authed.get("/api/autostart").json() == {"supported": False, "error": None, "tasks": {}}
        assert authed.post("/api/autostart/collector/enable").status_code == 400


def test_processes_include_admin_and_last_live(authed, ctx):
    ctx.started_at = "2026-11-18T10:00:00+00:00"
    authed.post("/api/processes/live/start", json=LIVE_BODY)
    authed.post("/api/processes/live/stop")
    data = authed.get("/api/processes").json()
    assert data["admin"]["running"] is True and data["admin"]["started_at"] == ctx.started_at
    assert data["live"]["running"] is False
    assert data["live_last"]["meta"]["profile"] == "donchian_fng"  # durduktan sonra da kalir


class TestPrepareLiveStart:
    def test_valid(self, db_session):
        cmd, meta = prepare_live_start(db_session, "donchian_fng", ["btc/usdt"], "1d", 5000)
        assert meta["symbols"] == ["BTC/USDT"] and meta["mode"] == "paper"
        assert cmd[cmd.index("--symbols") + 1] == "BTC/USDT"

    def test_rejects_injection_and_capital(self, db_session):
        for args in (("donchian_fng", ["BTC/USDT; rm"], "1d", 5000), ("donchian_fng", ["BTC/USDT"], "1d", 5),
                     ("yok", ["BTC/USDT"], "1d", 5000), ("donchian_fng", ["BTC/USDT"], "7x", 5000)):
            try:
                prepare_live_start(db_session, *args)
            except LiveStartError as exc:
                assert exc.status == 400
            else:
                raise AssertionError(f"kabul edilmemeliydi: {args}")

    def test_capital_mismatch_409(self, db_session):
        AccountRepository(db_session).register("paper", 10_000, mode="paper", profile="donchian_fng", timeframe="1d")
        try:
            prepare_live_start(db_session, "donchian_fng", ["BTC/USDT"], "1d", 20_000)
        except LiveStartError as exc:
            assert exc.status == 409
        else:
            raise AssertionError("sermaye uyusmazligi kabul edilmemeliydi")


class _Deferred:
    """Isleri hemen calistirmaz: iptal penceresini test etmek icin."""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        self.jobs.append((fn, args))

    def run_all(self):
        for fn, args in self.jobs:
            fn(*args)

    def shutdown(self, **_):
        pass


def test_cancel_queued_backtest(authed, ctx, db_session):
    ex = _Deferred()
    calls = []
    ctx.backtests = BacktestQueue(ctx.session_factory, runner=lambda p: calls.append(p) or (None, {}), executor=ex)
    run_id = authed.post("/api/backtests", json={"profile": "donchian", "symbols": ["BTC/USDT"],
                                                 "timeframe": "1d", "days": 400}).json()["id"]
    res = authed.post(f"/api/backtests/{run_id}/cancel")
    assert res.status_code == 200 and res.json()["status"] == "CANCELLED"
    ex.run_all()
    assert calls == []  # iptal edilen is calismadi
    assert db_session.get(BacktestRun, run_id).status == "CANCELLED"
    assert authed.post(f"/api/backtests/{run_id}/cancel").status_code == 409
    assert authed.post("/api/backtests/999999/cancel").status_code == 404


def test_running_backtest_cannot_be_cancelled(authed, ctx, db_session):
    run = BacktestRun(status="RUNNING", params="{}", created_by="ayse", started_at=datetime.now(UTC))
    db_session.add(run)
    db_session.commit()
    assert authed.post(f"/api/backtests/{run.id}/cancel").status_code == 409


# ------------------------------------------------- canli (gercek hesap) runner
class _Proc:
    def __init__(self):
        self.running, self.starts, self.meta, self.last = False, [], None, None

    def status(self, tail=5):
        from tradecrm.api.services.processes import ProcessStatus

        return ProcessStatus(self.running, 7 if self.running else None, None, None, "x.log", [], self.meta)

    def start(self, command=None, meta=None):
        self.running, self.meta = True, meta
        self.starts.append(command)
        self.last = {"started_at": "x", "meta": meta}
        return self.status()

    def stop(self):
        self.running = False
        return self.status()

    def last_start(self):
        return self.last


def _kucoin_proc(ctx):
    proc = _Proc()
    ctx.exchange_runners = {"kucoin": proc}
    return proc


def test_exchange_runner_listed_with_env_switch(authed, ctx):
    _kucoin_proc(ctx)
    data = authed.get("/api/processes").json()
    k = data["exchange_runners"]["kucoin"]
    assert k["running"] is False and k["last"] is None and k["env_orders_open"] is False


def test_exchange_runner_start_requires_cli_params_first(authed, ctx):
    _kucoin_proc(ctx)
    res = authed.post("/api/processes/exchange/kucoin/start")
    assert res.status_code == 409 and "live start --mode kucoin" in res.json()["detail"]


def test_exchange_runner_restarts_with_last_params_only(authed, ctx, db_session):
    proc = _kucoin_proc(ctx)
    proc.last = {"started_at": "x", "meta": {"profile": "donchian_fng", "symbols": ["BTC/USDT"], "timeframe": "1d",
                                             "initial_capital": 75.0}}
    res = authed.post("/api/processes/exchange/kucoin/start", json={"profile": "baska", "initial_capital": 9999})
    assert res.status_code == 200
    cmd = proc.starts[-1]
    assert cmd[cmd.index("--mode") + 1] == "kucoin" and cmd[cmd.index("--initial-capital") + 1] == "75.00"
    assert proc.meta["started_by"] == "ayse"
    assert authed.post("/api/processes/exchange/kucoin/start").status_code == 409  # zaten calisiyor
    assert authed.post("/api/processes/exchange/kucoin/stop").status_code == 200 and proc.running is False
    assert authed.post("/api/processes/exchange/kraken/start").status_code == 404


def test_exchange_runner_needs_csrf(authed, ctx):
    _kucoin_proc(ctx)
    del authed.headers["X-CSRF-Token"]
    assert authed.post("/api/processes/exchange/kucoin/stop").status_code == 403


def test_live_kucoin_autostart_needs_previous_start(authed, ctx, tmp_path):
    proc = _kucoin_proc(ctx)
    _svc(ctx, tmp_path=tmp_path)
    assert authed.post("/api/autostart/live_kucoin/enable").status_code == 409
    proc.last = {"started_at": "x", "meta": {"profile": "donchian_fng"}}
    assert authed.post("/api/autostart/live_kucoin/enable").status_code == 200


def test_live_account_value_from_last_runner_tick(authed, db_session):
    from tradecrm.db.models import EquitySnapshot

    AccountRepository(db_session).register("kucoin", 75.0, mode="live", profile="donchian_fng", timeframe="1d")
    acc = authed.get("/api/accounts/kucoin").json()
    assert acc["snapshot"] is None and "henuz kayit yok" in acc["snapshot_error"]
    db_session.add(EquitySnapshot(account="kucoin", ts=datetime.now(UTC), cash=60.0, allocated=15.0,
                                  market_value=16.5, equity=76.5, open_positions=1))
    db_session.commit()
    snap = authed.get("/api/accounts/kucoin").json()["snapshot"]
    assert snap["equity"] == 76.5 and snap["unrealized_pnl"] == pytest.approx(1.5) and snap["positions"] == 1
    assert snap["return_since_start"] == pytest.approx(76.5 / 75 - 1) and snap["as_of"]
