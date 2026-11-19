import pytest
from sqlalchemy.exc import OperationalError

from tradecrm.runner import autostart


def test_register_script_runs_collect_start_in_project_dir():
    script = autostart.build_register_script(r"C:\venv\Scripts\pythonw.exe", r"C:\Proje's\TradeCRM")
    assert "-Execute 'C:\\venv\\Scripts\\pythonw.exe'" in script
    assert "-Argument '-m tradecrm.cli collect start'" in script
    # Tek tirnak PowerShell'de ikilenerek kacirilir.
    assert "-WorkingDirectory 'C:\\Proje''s\\TradeCRM'" in script
    assert "-AtLogOn" in script and "$trigger.Delay = 'PT1M'" in script
    assert "-RunLevel Limited" in script  # yonetici yetkisi istemez
    assert "-MultipleInstances IgnoreNew" in script


def test_enable_rejects_non_project_dir(tmp_path):
    with pytest.raises(ValueError):
        autostart.enable(tmp_path)


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    with pytest.raises(autostart.AutostartUnsupported):
        autostart.status()


def _op_error():
    return OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))


def test_wait_for_database_retries_until_ready(monkeypatch):
    from tradecrm import cli
    from tradecrm.db import engine

    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _op_error()

    monkeypatch.setattr(engine, "create_db_and_tables", flaky)
    slept = []
    cli._wait_for_database("postgresql://x", max_wait_s=None, interval_s=30, sleep=slept.append)
    assert calls["n"] == 3 and slept == [30, 30]


def test_wait_for_database_single_try_raises(monkeypatch):
    from tradecrm import cli
    from tradecrm.db import engine

    def down(url):
        raise _op_error()

    monkeypatch.setattr(engine, "create_db_and_tables", down)
    with pytest.raises(OperationalError):
        cli._wait_for_database("postgresql://x", max_wait_s=0, sleep=lambda s: None)
