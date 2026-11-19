"""Arka plan sureclerini (veri toplayici, paper live runner) admin
panelinden baslatip durdurmak icin kucuk bir yonetici.

Toplayici AYRI bir surec olarak baslatilir: dashboard kapansa bile
calismaya devam eder (Binance OI verisini sadece ~30 gun sakliyor;
toplayicinin surekliligi onemli). Durum bir PID dosyasinda tutulur.
"""

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # Not: Windows'ta os.kill(pid, 0) sureci OLDURUR (TerminateProcess) -- kullanma.
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Windows'ta "double-spawn": dashboard kisa omurlu bir baslatici calistirir,
# baslatici asil sureci baslatip PID'ini yazar ve hemen cikar. Boylece asil
# surecin ust sureci (baslatici) olmus olur ve dashboard'un surec agaci
# oldurulse de (taskkill /T, IDE/terminal kapanmasi) toplayici yasar.
# Job Object'e karsi da breakaway denenir (izin yoksa normal baslatilir).
_WIN_LAUNCHER = r"""
import json, subprocess, sys
spec = json.loads(sys.argv[1])
log = open(spec["log"], "a", encoding="utf-8")
kw = dict(stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008
try:
    p = subprocess.Popen(spec["command"], creationflags=flags | 0x01000000, **kw)
except OSError:
    p = subprocess.Popen(spec["command"], creationflags=flags, **kw)
print(p.pid, flush=True)
"""


def _spawn(command: list[str], log_path: Path, env: dict) -> int:
    """Sureci arka planda, ust surecten bagimsiz baslatir; PID dondurur."""
    if sys.platform == "win32":
        out = subprocess.run(
            [sys.executable, "-c", _WIN_LAUNCHER, json.dumps({"command": command, "log": str(log_path)})],
            env=env, capture_output=True, text=True, timeout=30, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return int(out.stdout.strip().splitlines()[-1])
    with open(log_path, "a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return proc.pid


def _terminate_tree(pid: int) -> None:
    """Sureci ALT SURECLERIYLE birlikte sonlandirir.

    Windows'ta venv'in python.exe/pythonw.exe'si asil yorumlayiciyi alt surec
    olarak baslatan bir yonlendiricidir; PID dosyasindaki PID yonlendiricinin.
    Yonlendirici olunce alt surecini de kapatiyor (2026-11 olculdu), ama bu
    davranisa guvenmek yerine agac acikca sonlandirilir: sahipsiz kalan bir
    toplayici/runner sonraki "baslat"ta ikinci kopya demek olurdu."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)  # start_new_session: kendi surec grubu
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


@dataclass
class ProcessStatus:
    running: bool
    pid: int | None
    started_at: str | None
    command: list[str] | None
    log_path: str
    last_log_lines: list[str]
    meta: dict | None = None


class ManagedProcess:
    def __init__(self, name: str, command: list[str], state_dir: str = "state", log_dir: str = "logs") -> None:
        self.name = name
        self.command = command
        self.pid_path = Path(state_dir) / f"{name}.pid"
        # Son basarili baslatmanin parametreleri: surec durunca da kalir
        # (otomatik baslatma "live resume" buradan okur).
        self.last_path = Path(state_dir) / f"{name}.last.json"
        self.log_path = Path(log_dir) / f"{name}.log"

    # ----------------------------------------------------------------- durum
    def _read_state(self) -> dict | None:
        try:
            return json.loads(self.pid_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def status(self, tail: int = 5) -> ProcessStatus:
        state = self._read_state()
        running = bool(state) and _pid_alive(int(state.get("pid", 0)))
        if state and not running:
            # Surec olmus (yeniden baslatma, cokme): bayat PID dosyasini temizle.
            self.pid_path.unlink(missing_ok=True)
            state = None
        return ProcessStatus(
            running=running,
            pid=state["pid"] if state else None,
            started_at=state.get("started_at") if state else None,
            command=state.get("command") if state else None,
            log_path=str(self.log_path),
            last_log_lines=self._tail(tail),
            meta=state.get("meta") if state else None,
        )

    def _tail(self, n: int) -> list[str]:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return lines[-n:]

    # --------------------------------------------------------------- kontrol
    def start(self, command: list[str] | None = None, meta: dict | None = None) -> ProcessStatus:
        """command verilirse bu calistirma icin onu kullanir (orn. runner
        parametreleri). Calisan bir kopya varsa yenisini BASLATMAZ."""
        current = self.status()
        if current.running:
            return current  # idempotent: ikinci bir kopya baslatma
        if command is not None:
            self.command = command
        if not self.command:
            raise ValueError(f"{self.name}: komut yok")
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as log:
            log.write(f"--- {datetime.now(UTC).isoformat()} baslatildi: {' '.join(self.command)}\n")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        pid = _spawn(self.command, self.log_path, env)
        started_at = datetime.now(UTC).isoformat()
        self.pid_path.write_text(
            json.dumps({"pid": pid, "started_at": started_at, "command": self.command, "meta": meta}),
            encoding="utf-8",
        )
        self.last_path.write_text(json.dumps({"started_at": started_at, "meta": meta}), encoding="utf-8")
        return self.status()

    def last_start(self) -> dict | None:
        """Son baslatmanin {started_at, meta} kaydi; hic baslatilmadiysa None."""
        try:
            return json.loads(self.last_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def stop(self) -> ProcessStatus:
        state = self._read_state()
        if state and _pid_alive(int(state["pid"])):
            _terminate_tree(int(state["pid"]))
            with open(self.log_path, "a", encoding="utf-8") as log:
                log.write(f"--- {datetime.now(UTC).isoformat()} panelden durduruldu\n")
        self.pid_path.unlink(missing_ok=True)
        return self.status()


def collector_process() -> ManagedProcess:
    """Semboller ve saklama suresi .env'den (COLLECT_SYMBOLS, COLLECT_RETENTION_DAYS)."""
    return ManagedProcess("collector", [sys.executable, "-m", "tradecrm.cli", "collect", "run"])


def admin_process() -> ManagedProcess:
    """Admin paneli sunucusu (otomatik baslatma `admin start` ile)."""
    return ManagedProcess("admin", [sys.executable, "-m", "tradecrm.cli", "admin", "serve"])


def live_runner_process() -> ManagedProcess:
    """Paper live runner: komut her baslatmada dogrulanmis parametrelerden kurulur."""
    return ManagedProcess("live", [])


def exchange_runner_process(exchange_id: str) -> ManagedProcess:
    """Canli (gercek hesap) runner: state/live_<borsa>.pid, logs/live_<borsa>.log."""
    return ManagedProcess(f"live_{exchange_id}", [])


def build_live_command(profile: str, symbols: list[str], timeframe: str, initial_capital: float,
                       mode: str = "paper") -> list[str]:
    return [
        sys.executable, "-m", "tradecrm.cli", "live", "run", "--mode", mode,
        "--profile", profile, "--symbols", ",".join(symbols), "--timeframe", timeframe,
        "--initial-capital", f"{initial_capital:.2f}",
    ]
