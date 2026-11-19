import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from tradecrm.api.security import LoginThrottle, hash_password, verify_password
from tradecrm.db.models import AdminAction, AdminSession
from tradecrm.db.repository import AccountRepository, TradeRepository
from tradecrm.risk.state import RiskStateStore

PASSWORD = "dogru-parola-123"  # conftest ile ayni


# ------------------------------------------------------------------ guvenlik
class TestPasswords:
    def test_hash_roundtrip_and_salt(self):
        a, b = hash_password("parola-uzun-123"), hash_password("parola-uzun-123")
        assert a != b  # tuz
        assert verify_password("parola-uzun-123", a)
        assert not verify_password("yanlis", a)
        assert not verify_password("x", "bozuk$hash")

    def test_throttle_locks_after_failures(self):
        now = [0.0]
        t = LoginThrottle(max_failures=3, lockout_seconds=60, clock=lambda: now[0])
        for _ in range(3):
            t.record_failure("Ayse", "1.1.1.1")
        assert t.retry_after("ayse", "1.1.1.1") > 0
        assert t.retry_after("ayse", "2.2.2.2") == 0  # baska IP etkilenmez
        now[0] = 61
        assert t.retry_after("ayse", "1.1.1.1") == 0


class TestAuth:
    def test_api_requires_login(self, client):
        for path in ("/api/overview", "/api/accounts", "/api/trades", "/api/processes", "/api/users"):
            assert client.get(path).status_code == 401, path

    def test_wrong_password_same_error_as_unknown_user(self, client, user):
        a = client.post("/api/auth/login", json={"username": "ayse", "password": "yanlis-parola-xx"})
        b = client.post("/api/auth/login", json={"username": "yok", "password": "yanlis-parola-xx"})
        assert a.status_code == b.status_code == 401
        assert a.json() == b.json()

    def test_lockout(self, client, user):
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "ayse", "password": "yanlis-parola-xx"})
        res = client.post("/api/auth/login", json={"username": "ayse", "password": PASSWORD})
        assert res.status_code == 429  # dogru parola bile kilit suresince reddedilir

    def test_login_sets_httponly_strict_cookie(self, client, user):
        res = client.post("/api/auth/login", json={"username": "ayse", "password": PASSWORD})
        cookie = res.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.get("/api/auth/me").json()["username"] == "ayse"

    def test_session_token_not_stored_in_plain(self, client, user, db_session):
        client.post("/api/auth/login", json={"username": "ayse", "password": PASSWORD})
        token = client.cookies.get("tc_session")
        rows = list(db_session.exec(select(AdminSession)))
        assert rows and all(r.token_hash != token for r in rows)

    def test_expired_session_rejected(self, authed, db_session):
        for row in db_session.exec(select(AdminSession)):
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db_session.add(row)
        db_session.commit()
        assert authed.get("/api/overview").status_code == 401

    def test_logout_revokes(self, authed):
        assert authed.post("/api/auth/logout").status_code == 200
        assert authed.get("/api/overview").status_code == 401

    def test_inactive_user_cannot_use_session(self, authed, user, db_session):
        user.is_active = False
        db_session.add(user)
        db_session.commit()
        assert authed.get("/api/overview").status_code == 401

    def test_mutations_require_csrf(self, authed):
        authed.headers.pop("X-CSRF-Token")
        res = authed.post("/api/risk/paper/trading", json={"enabled": False, "confirm": "paper"})
        assert res.status_code == 403
        authed.headers["X-CSRF-Token"] = "baska-token"
        assert authed.post("/api/processes/collector/start").status_code == 403

    def test_security_headers(self, client):
        res = client.get("/")
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
        assert res.headers["x-frame-options"] == "DENY"


# ------------------------------------------------------------------ izleme
def _seed(db_session):
    AccountRepository(db_session).register("paper", 10_000.0, profile="donchian_fng", timeframe="1d")
    repo = TradeRepository(db_session)
    t = repo.open_trade(symbol="BTC/USDT", strategy_name="s", broker="paper", entry_time=datetime.now(UTC) - timedelta(days=2),
                        entry_price=80.0, qty=10.0, account_balance_at_entry=10_000.0, entry_fees=0.8)
    closed = repo.open_trade(symbol="ETH/USDT", strategy_name="s", broker="paper",
                             entry_time=datetime.now(UTC) - timedelta(days=5), entry_price=50.0, qty=4.0,
                             account_balance_at_entry=10_000.0)
    repo.close_trade(closed.id, exit_time=datetime.now(UTC) - timedelta(days=1), exit_price=55.0, fees=0.2,
                     exit_reason="trailing_stop")
    return t


class TestMonitoring:
    def test_overview_uses_paper_accounting(self, authed, db_session):
        _seed(db_session)
        data = authed.get("/api/overview").json()
        acc = next(a for a in data["accounts"] if a["name"] == "paper")
        snap = acc["snapshot"]
        # nakit = 10.000 + gerceklesen (20 - 0.2) - acik maliyet (800 + 0.8)
        assert snap["cash"] == pytest.approx(10_000 + 19.8 - 800.8)
        assert snap["market_value"] == pytest.approx(1_000)  # fiyat 100
        assert acc["flags"]["buy_allowed"] is False and "DB alim izni kapali" in acc["flags"]["blockers"]
        assert data["totals"]["positions"] == 1

    def test_positions_mark_to_market(self, authed, db_session):
        _seed(db_session)
        [pos] = authed.get("/api/positions").json()
        assert pos["unrealized_pnl"] == pytest.approx(1_000 - 800.8)

    def test_trades_paging_and_csv(self, authed, db_session):
        _seed(db_session)
        data = authed.get("/api/trades?size=1").json()
        assert data["total"] == 2 and len(data["items"]) == 1
        assert data["summary"]["closed"] == 1
        csv_text = authed.get("/api/trades.csv?status=CLOSED").text
        assert "ETH/USDT" in csv_text and "BTC/USDT" not in csv_text

    def test_chart(self, authed, db_session):
        t = _seed(db_session)
        data = authed.get(f"/api/trades/{t.id}/chart").json()
        assert data["timeframe"] == "1d" and len(data["candles"]) == 30
        assert authed.get(f"/api/trades/{t.id}/chart?timeframe=7x").status_code == 400

    def test_audit_log_tail(self, authed, ctx):
        with open(ctx.audit_log_path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps({"event_type": "signal_evaluated", "symbol": "BTC/USDT", "i": i}) + "\n" for i in range(5))
            f.write("bozuk satir\n")
        data = authed.get("/api/audit/log?limit=2").json()
        assert [r["i"] for r in data["items"]] == [4, 3]

    def test_unknown_api_path_is_json_404(self, authed):
        res = authed.get("/api/yok")
        assert res.status_code == 404 and res.headers["content-type"].startswith("application/json")


# ------------------------------------------------------------------ risk
class TestRiskActions:
    def test_enable_paper_writes_event_and_action(self, authed, db_session):
        res = authed.post("/api/risk/paper/trading", json={"enabled": True, "confirm": "paper"})
        assert res.status_code == 200
        assert RiskStateStore(db_session, broker="paper").is_trading_enabled() is True
        actions = [a.action for a in db_session.exec(select(AdminAction))]
        assert "trading_enabled" in actions

    def test_confirm_must_match(self, authed, db_session):
        res = authed.post("/api/risk/paper/trading", json={"enabled": True, "confirm": "pap"})
        assert res.status_code == 400
        assert RiskStateStore(db_session, broker="paper").is_trading_enabled() is False

    def test_live_account_safe_direction_only(self, authed, db_session):
        RiskStateStore(db_session, broker="binance").set_trading_enabled(False)  # hesap bilinsin
        assert authed.post("/api/risk/binance/trading", json={"enabled": True, "confirm": "binance"}).status_code == 403
        assert RiskStateStore(db_session, broker="binance").is_trading_enabled() is False
        assert authed.post("/api/risk/binance/kill-switch/trip", json={"confirm": "binance", "reason": "test"}).status_code == 200
        assert authed.post("/api/risk/binance/kill-switch/reset", json={"confirm": "binance"}).status_code == 403
        assert RiskStateStore(db_session, broker="binance").is_kill_switch_tripped() is True

    def test_unknown_account(self, authed):
        assert authed.post("/api/risk/hayalet/trading", json={"enabled": False, "confirm": "hayalet"}).status_code == 404


# ------------------------------------------------------------------ surecler
class TestProcesses:
    def test_live_start_validates_and_builds_arg_list(self, authed, ctx):
        res = authed.post("/api/processes/live/start", json={
            "profile": "donchian_fng", "symbols": ["btc/usdt", "ETH/USDT"], "timeframe": "1d", "initial_capital": 10000})
        assert res.status_code == 200, res.text
        cmd = ctx.live.starts[-1]
        assert isinstance(cmd, list) and "BTC/USDT,ETH/USDT" in cmd and "--mode" in cmd and "paper" in cmd
        assert res.json()["meta"]["started_by"] == "ayse"
        assert "command" not in res.json()

    @pytest.mark.parametrize("body", [
        {"profile": "donchian_fng", "symbols": ["BTC/USDT; rm -rf /"], "timeframe": "1d", "initial_capital": 10000},
        {"profile": "yok", "symbols": ["BTC/USDT"], "timeframe": "1d", "initial_capital": 10000},
        {"profile": "donchian_fng", "symbols": ["BTC/USDT"], "timeframe": "2w", "initial_capital": 10000},
        {"profile": "donchian_fng", "symbols": ["BTC/USDT"], "timeframe": "1d", "initial_capital": 5},
    ])
    def test_live_start_rejects_bad_params(self, authed, ctx, body):
        assert authed.post("/api/processes/live/start", json=body).status_code in (400, 422)
        assert ctx.live.starts == []

    def test_single_runner(self, authed, ctx):
        ctx.live.running = True
        body = {"profile": "donchian_fng", "symbols": ["BTC/USDT"], "timeframe": "1d", "initial_capital": 10000}
        assert authed.post("/api/processes/live/start", json=body).status_code == 409

    def test_capital_mismatch_refused(self, authed, ctx, db_session):
        AccountRepository(db_session).register("paper", 10_000.0)
        body = {"profile": "donchian_fng", "symbols": ["BTC/USDT"], "timeframe": "1d", "initial_capital": 20000}
        res = authed.post("/api/processes/live/start", json=body)
        assert res.status_code == 409 and "10,000.00" in res.json()["detail"]

    def test_collector(self, authed, ctx):
        assert authed.post("/api/processes/collector/start").json()["running"] is True
        assert authed.post("/api/processes/bilinmeyen/start").status_code in (404, 405)


# ------------------------------------------------------------------ backtest
class TestBacktests:
    def test_lifecycle_and_baseline(self, authed, ctx):
        res = authed.post("/api/backtests", json={"profile": "donchian", "symbols": ["BTC/USDT"], "timeframe": "1d",
                                                  "days": 365, "train_days": 120, "test_days": 30})
        assert res.status_code == 200, res.text
        run = authed.get(f"/api/backtests/{res.json()['id']}").json()
        assert run["status"] == "DONE" and run["summary"]["gate_passed"] is False
        assert "returns" not in run["result"]
        saved = authed.post(f"/api/backtests/{run['id']}/save-baseline").json()
        assert saved["n"] == 15
        assert (ctx.baseline_dir / "donchian__1d.json").exists()
        assert authed.get("/api/baselines").json()[0]["n"] == 15

    def test_invalid_params(self, authed):
        res = authed.post("/api/backtests", json={"profile": "donchian", "symbols": ["BTC"], "timeframe": "1d"})
        assert res.status_code == 400

    def test_failure_recorded(self, authed, ctx):
        def boom(_params):
            raise RuntimeError("veri yok")

        ctx.backtests.runner = boom
        run_id = authed.post("/api/backtests", json={"profile": "donchian", "symbols": ["BTC/USDT"]}).json()["id"]
        run = authed.get(f"/api/backtests/{run_id}").json()
        assert run["status"] == "FAILED" and "veri yok" in run["error"]

    def test_interrupted_jobs_marked(self, ctx, db_session):
        from tradecrm.db.models import BacktestRun

        db_session.add(BacktestRun(params="{}", created_by="x", status="RUNNING"))
        db_session.commit()
        assert ctx.backtests.mark_interrupted() == 1


# ------------------------------------------------------------------ kullanicilar
class TestUsers:
    def test_create_and_deactivate(self, authed):
        res = authed.post("/api/users", json={"username": "Mehmet", "password": "baska-parola-456"})
        assert res.status_code == 200 and res.json()["username"] == "mehmet"
        assert authed.post("/api/users", json={"username": "kisa", "password": "123"}).status_code == 400
        uid = res.json()["id"]
        assert authed.patch(f"/api/users/{uid}", json={"is_active": False}).json()["is_active"] is False

    def test_last_active_user_protected(self, authed, user):
        res = authed.patch(f"/api/users/{user.id}", json={"is_active": False})
        assert res.status_code == 400

    def test_password_change_revokes_sessions(self, authed, user):
        assert authed.patch(f"/api/users/{user.id}", json={"password": "yepyeni-parola-789"}).status_code == 200
        assert authed.get("/api/overview").status_code == 401


def test_no_secrets_in_responses(authed, db_session, monkeypatch):
    """Hicbir API yaniti .env sirlarini icermemeli."""
    _seed(db_session)
    secret = "COK-GIZLI-ANAHTAR-123"
    for var in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.setenv(var, secret)
    bodies = [authed.get(p).text for p in (
        "/api/overview", "/api/accounts", "/api/positions", "/api/trades", "/api/orders", "/api/processes",
        "/api/risk/events", "/api/audit/admin-actions", "/api/data/sources", "/api/profiles", "/api/users",
    )]
    assert all(secret not in b for b in bodies)
    assert all("password_hash" not in b and "token_hash" not in b for b in bodies)
