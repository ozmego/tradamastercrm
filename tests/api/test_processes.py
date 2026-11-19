import sys
import time

import pytest

from tradecrm.api.services.processes import (
    ManagedProcess,
    _pid_alive,
    build_live_command,
)


@pytest.fixture()
def sleeper(tmp_path):
    proc = ManagedProcess(
        "sleeper",
        [sys.executable, "-c", "import time; print('basladi', flush=True); time.sleep(60)"],
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
    )
    yield proc
    proc.stop()


def _wait(pred, tries=50):
    for _ in range(tries):
        if pred():
            return True
        time.sleep(0.1)
    return pred()


class TestManagedProcess:
    def test_start_status_stop(self, sleeper):
        assert sleeper.status().running is False
        st = sleeper.start(meta={"profile": "x"})
        assert st.running is True and st.pid and st.meta == {"profile": "x"}
        pid = st.pid
        assert sleeper.start().pid == pid  # ikinci kopya baslatilmaz
        assert sleeper.stop().running is False
        assert _wait(lambda: not _pid_alive(pid))

    def test_stop_kills_real_interpreter_not_only_launcher(self, tmp_path):
        """Windows venv: PID dosyasindaki surec yonlendirici; asil yorumlayici
        onun alt sureci. Durdurunca asil surec de olmeli (sahipsiz kopya kalmamali)."""
        pid_file = tmp_path / "real.pid"
        code = (f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                "time.sleep(60)")
        proc = ManagedProcess("child", [sys.executable, "-c", code], state_dir=str(tmp_path / "s"),
                              log_dir=str(tmp_path / "l"))
        proc.start()
        assert _wait(lambda: pid_file.exists() and pid_file.read_text().strip() != "")
        real = int(pid_file.read_text())
        proc.stop()
        assert _wait(lambda: not _pid_alive(real)), "asil surec sahipsiz kaldi"

    def test_stale_pid_file_cleaned(self, sleeper):
        sleeper.pid_path.parent.mkdir(parents=True, exist_ok=True)
        sleeper.pid_path.write_text('{"pid": 999999, "started_at": "x", "command": []}', encoding="utf-8")
        assert sleeper.status().running is False
        assert not sleeper.pid_path.exists()

    def test_log_captured(self, sleeper):
        sleeper.start()
        assert _wait(lambda: any("basladi" in line for line in sleeper.status().last_log_lines))

    def test_start_without_command_fails(self, tmp_path):
        proc = ManagedProcess("bos", [], state_dir=str(tmp_path), log_dir=str(tmp_path))
        with pytest.raises(ValueError):
            proc.start()


def test_live_command_is_arg_list():
    cmd = build_live_command("donchian_fng", ["BTC/USDT", "ETH/USDT"], "1d", 10_000)
    assert cmd[1:5] == ["-m", "tradecrm.cli", "live", "run"]
    assert cmd[cmd.index("--symbols") + 1] == "BTC/USDT,ETH/USDT"
    assert cmd[cmd.index("--mode") + 1] == "paper"


def test_last_start_survives_stop(tmp_path):
    proc = ManagedProcess("x", [sys.executable, "-c", "import time; time.sleep(30)"],
                          state_dir=str(tmp_path / "s"), log_dir=str(tmp_path / "l"))
    assert proc.last_start() is None
    proc.start(meta={"profile": "donchian_fng"})
    proc.stop()
    assert proc.status().running is False
    assert proc.last_start()["meta"] == {"profile": "donchian_fng"}
