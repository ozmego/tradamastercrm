"""Arka plan sureclerinin Windows oturumu acilinca kendiliginden baslamasi.

Her surec icin Gorev Zamanlayici'ya kullanici duzeyinde (yonetici yetkisi
gerekmez) bir gorev kaydedilir. Gorevler kisa omurlu baslaticilardir:
`collect start`, `live resume`, `admin start` -- hepsi sureci panelin
kullandigi ManagedProcess ile baslatir (state/<ad>.pid, logs/<ad>.log) ve
zaten calisiyorsa ikinci kopya acmaz. Calisma dizini proje koku olur ki
.env, state/ ve logs/ dogru yerde bulunsun.

Veritabani (Docker) oturum acilisinda gec gelebilir: `collect run` ve
`live resume` baglanti kurulana kadar bekler.
"""

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    key: str
    task_name: str
    arguments: str
    delay: str  # ISO 8601 sure: oturum acildiktan sonra
    description: str


TASKS: dict[str, TaskSpec] = {
    "collector": TaskSpec("collector", "TradeCRM Veri Toplayici", "-m tradecrm.cli collect start", "PT1M",
                          "TradeCRM open interest / long-short toplayicisini oturum acilisinda baslatir"),
    "live": TaskSpec("live", "TradeCRM Paper Runner", "-m tradecrm.cli live resume", "PT2M",
                     "TradeCRM paper runner'i son parametreleriyle oturum acilisinda baslatir"),
    "admin": TaskSpec("admin", "TradeCRM Admin Paneli", "-m tradecrm.cli admin start", "PT1M",
                      "TradeCRM admin panelini oturum acilisinda baslatir"),
    "live_kucoin": TaskSpec("live_kucoin", "TradeCRM KuCoin Runner", "-m tradecrm.cli live resume --mode kucoin",
                            "PT3M", "TradeCRM KuCoin (gercek hesap) runner'ini son parametreleriyle baslatir; "
                            "emir salterleri ayrica gerekir"),
}

# Geriye donuk uyumluluk (collect autostart)
TASK_NAME = TASKS["collector"].task_name
AUTOSTART_DELAY = TASKS["collector"].delay


class AutostartUnsupported(RuntimeError):
    pass


@dataclass
class AutostartStatus:
    installed: bool
    state: str | None = None  # Ready | Running | Disabled ...
    last_run: str | None = None
    last_result: int | None = None
    next_run: str | None = None
    command: str | None = None
    working_dir: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def get_task(key: str) -> TaskSpec:
    if key not in TASKS:
        raise KeyError(key)
    return TASKS[key]


def supported() -> bool:
    return sys.platform == "win32"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def background_python() -> str:
    """Konsol penceresi acmayan yorumlayici (pythonw), yoksa python."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.is_file() else exe)


def _run_ps(script: str, timeout: int = 60) -> str:
    if not supported():
        raise AutostartUnsupported("Otomatik baslatma yalnizca Windows'ta (Gorev Zamanlayici) destekleniyor.")
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip() or f"powershell cikis kodu {out.returncode}")
    return out.stdout


def build_register_script(python: str, working_dir: str, task_name: str = TASK_NAME,
                          delay: str = AUTOSTART_DELAY, arguments: str = TASKS["collector"].arguments,
                          description: str = TASKS["collector"].description) -> str:
    user = '"$env:USERDOMAIN\\$env:USERNAME"'
    action = (f"$action = New-ScheduledTaskAction -Execute {_ps_quote(python)} "
              f"-Argument {_ps_quote(arguments)} -WorkingDirectory {_ps_quote(working_dir)}")
    settings = ("$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                "-StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew")
    principal = f"$principal = New-ScheduledTaskPrincipal -UserId {user} -LogonType Interactive -RunLevel Limited"
    register = (f"Register-ScheduledTask -TaskName {_ps_quote(task_name)} -Action $action -Trigger $trigger "
                f"-Settings $settings -Principal $principal -Description {_ps_quote(description)} "
                "-Force | Out-Null")
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        action,
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User {user}",
        f"$trigger.Delay = {_ps_quote(delay)}",
        settings,
        principal,
        register,
    ])


def enable(working_dir: Path, key: str = "collector") -> AutostartStatus:
    spec = get_task(key)
    working_dir = working_dir.resolve()
    if not (working_dir / "pyproject.toml").is_file():
        raise ValueError(f"{working_dir} proje koku degil (pyproject.toml yok)")
    _run_ps(build_register_script(background_python(), str(working_dir), spec.task_name, spec.delay,
                                  spec.arguments, spec.description))
    return status(key)


def disable(key: str = "collector") -> bool:
    """Gorevi siler; yoksa False."""
    spec = get_task(key)
    if not status(key).installed:
        return False
    _run_ps(f"Unregister-ScheduledTask -TaskName {_ps_quote(spec.task_name)} -Confirm:$false")
    return True


def _status_script(task_names: list[str]) -> str:
    names = ", ".join(_ps_quote(n) for n in task_names)
    return "\n".join([
        "$out = @{}",
        f"foreach ($n in @({names})) {{",
        "  $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue",
        "  if ($null -eq $t) { $out[$n] = $null; continue }",
        "  $i = Get-ScheduledTaskInfo -TaskName $n",
        "  $a = $t.Actions | Select-Object -First 1",
        "  $out[$n] = @{ state = [string]$t.State; last_run = [string]$i.LastRunTime;",
        "    last_result = [int]$i.LastTaskResult; next_run = [string]$i.NextRunTime;",
        "    command = ($a.Execute + ' ' + $a.Arguments); working_dir = $a.WorkingDirectory }",
        "}",
        "$out | ConvertTo-Json -Compress -Depth 4",
    ])


def _parse(data: dict | None) -> AutostartStatus:
    if not data:
        return AutostartStatus(installed=False)
    # Gorev hic calismadiysa LastRunTime 1999/11/30 gibi bir yer tutucudur.
    last_run = data.get("last_run") or None
    if last_run and "1999" in last_run:
        last_run = None
    return AutostartStatus(installed=True, state=data.get("state"), last_run=last_run,
                           last_result=data.get("last_result") if last_run else None,
                           next_run=data.get("next_run") or None, command=data.get("command"),
                           working_dir=data.get("working_dir"))


def status_all() -> dict[str, AutostartStatus]:
    """Butun gorevler tek PowerShell cagrisiyla."""
    raw = json.loads(_run_ps(_status_script([t.task_name for t in TASKS.values()])).strip() or "{}")
    return {key: _parse(raw.get(spec.task_name)) for key, spec in TASKS.items()}


def status(key: str = "collector") -> AutostartStatus:
    spec = get_task(key)
    raw = json.loads(_run_ps(_status_script([spec.task_name])).strip() or "{}")
    return _parse(raw.get(spec.task_name))
