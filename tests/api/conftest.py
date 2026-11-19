from contextlib import nullcontext
from dataclasses import dataclass, field

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradecrm.api.app import create_app
from tradecrm.api.context import AppContext
from tradecrm.api.security import LoginThrottle
from tradecrm.api.services.processes import ProcessStatus
from tradecrm.api.services.users import create_user

PASSWORD = "dogru-parola-123"


class FakeMarket:
    def __init__(self, price=100.0):
        self.price = price

    def fetch_latest_price(self, symbol):
        return self.price

    def fetch_latest_ohlcv(self, symbol, timeframe="1h", limit=200):
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("h"), periods=30, freq="h")
        return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1.0}, index=idx)


@dataclass
class FakeProc:
    running: bool = False
    starts: list = field(default_factory=list)
    meta: dict | None = None

    def status(self, tail=5):
        return ProcessStatus(self.running, 42 if self.running else None, None, ["python", "x"], "x.log", [], self.meta)

    def start(self, command=None, meta=None):
        self.running = True
        self.meta = meta
        self.starts.append(command)
        self.last = {"started_at": "2026-11-18T00:00:00+00:00", "meta": meta}
        return self.status()

    def last_start(self):
        return getattr(self, "last", None)

    def stop(self):
        self.running = False
        return self.status()


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, **_):
        pass


@pytest.fixture()
def ctx(db_session, tmp_path):
    from tradecrm.api.services.backtests import BacktestQueue

    factory = lambda: nullcontext(db_session)
    return AppContext(
        session_factory=factory, market=FakeMarket(), collector=FakeProc(), live=FakeProc(),
        backtests=BacktestQueue(factory, runner=lambda p: (None, _fake_result(p)), executor=InlineExecutor()),
        audit_log_path=str(tmp_path / "audit.jsonl"), baseline_dir=tmp_path / "baselines",
        static_dir=tmp_path / "static", throttle=LoginThrottle(),
    )


def _fake_result(params):
    return {
        "profile": params.profile, "pooled": {"n_trades": 3, "profit_factor": 1.5}, "monte_carlo": {},
        "gate": {"passed": False, "checks": {"min_trades": False}, "failures": ["az islem"]},
        "per_symbol": [], "equity": [], "trades": [], "returns": [0.1, -0.03, 0.05] * 5,
    }


@pytest.fixture()
def client(ctx):
    return TestClient(create_app(ctx))


@pytest.fixture()
def user(db_session):
    return create_user(db_session, "ayse", PASSWORD)


@pytest.fixture()
def authed(client, user):
    """Giris yapmis istemci; CSRF basligi varsayilan olarak eklenir."""
    res = client.post("/api/auth/login", json={"username": "ayse", "password": PASSWORD})
    assert res.status_code == 200, res.text
    client.headers["X-CSRF-Token"] = res.json()["csrf_token"]
    return client
