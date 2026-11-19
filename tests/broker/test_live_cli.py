"""Canli mod CLI: kimlik bilgisi eksikse acik hata, emir/baglanti denenmez."""

import pytest
from typer.testing import CliRunner

from tradecrm.cli import app

runner = CliRunner()
KEYS = ("KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_API_PASSPHRASE", "BINANCE_API_KEY", "BINANCE_API_SECRET")


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for k in KEYS:
        monkeypatch.setenv(k, "")


def test_live_check_kucoin_requires_passphrase(monkeypatch):
    monkeypatch.setenv("KUCOIN_API_KEY", "k")
    monkeypatch.setenv("KUCOIN_API_SECRET", "s")
    res = runner.invoke(app, ["live", "check", "--exchange", "kucoin", "--initial-capital", "100"])
    assert res.exit_code == 1 and "KUCOIN_API_PASSPHRASE" in res.output


def test_live_run_kucoin_without_keys_exits():
    res = runner.invoke(app, ["live", "run", "--mode", "kucoin", "--profile", "donchian_fng", "--symbols", "BTC/USDT",
                              "--timeframe", "1d", "--initial-capital", "100"])
    assert res.exit_code == 1 and "KUCOIN_API_KEY" in res.output


def test_live_check_unknown_exchange():
    res = runner.invoke(app, ["live", "check", "--exchange", "btcturk", "--initial-capital", "100"])
    assert res.exit_code == 1 and "desteklenmeyen borsa" in res.output


def test_live_run_rejects_unknown_mode():
    res = runner.invoke(app, ["live", "run", "--mode", "kraken"])
    assert res.exit_code == 1 and "paper | kucoin | binance" in res.output


class _Proc:
    def __init__(self, meta):
        self.running, self.started = False, []
        self.last = {"started_at": "x", "meta": meta}

    def status(self, tail=0):
        from types import SimpleNamespace

        return SimpleNamespace(running=self.running, pid=1, log_path="x.log")

    def last_start(self):
        return self.last

    def start(self, command=None, meta=None):
        self.running = True
        self.started.append((command, meta))
        return SimpleStatus()


class SimpleStatus:
    pid = 1
    log_path = "x.log"


@pytest.mark.parametrize("mode", ["paper", "kucoin"])
def test_resume_starts_runner_even_if_database_is_down(monkeypatch, mode):
    """Oturum acilisinda DB gec gelirse baslatici beklemeye takilip (Gorev Zamanlayici sure siniri)
    olmemeli: runner'i baslatir, DB'yi runner bekler."""
    from sqlalchemy.exc import OperationalError

    from tradecrm import cli
    from tradecrm.api.services import processes

    capital = 10_000.0 if mode == "paper" else 75.0  # paper alt siniri 100 USDT
    proc = _Proc({"profile": "donchian_fng", "symbols": ["BTC/USDT"], "timeframe": "1d", "initial_capital": capital})
    monkeypatch.setattr(processes, "live_runner_process", lambda: proc)
    monkeypatch.setattr(processes, "exchange_runner_process", lambda eid: proc)

    def down(**kwargs):
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))

    monkeypatch.setattr(cli, "_collect_session", down)
    res = runner.invoke(app, ["live", "resume", "--mode", mode])
    assert res.exit_code == 0, res.output
    assert "DB'yi kendisi bekleyecek" in res.output
    [(command, meta)] = proc.started
    assert command[command.index("--mode") + 1] == mode and meta["started_by"] == "autostart"


def test_resume_still_rejects_invalid_params_without_db(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from tradecrm import cli
    from tradecrm.api.services import processes

    proc = _Proc({"profile": "yok_boyle_profil", "symbols": ["BTC/USDT"], "timeframe": "1d", "initial_capital": 75.0})
    monkeypatch.setattr(processes, "exchange_runner_process", lambda eid: proc)
    monkeypatch.setattr(cli, "_collect_session",
                        lambda **kw: (_ for _ in ()).throw(OperationalError("x", {}, Exception("down"))))
    res = runner.invoke(app, ["live", "resume", "--mode", "kucoin"])
    assert res.exit_code == 1 and proc.started == []
