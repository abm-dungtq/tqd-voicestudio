"""Start/stop the local TQD-VoiceStudio server in the background."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from tqd_voicestudio import config, netsec, paths
from tqd_voicestudio.config import Config


def base_url(cfg: Config) -> str:
    return f"http://{netsec.connect_host(cfg.host)}:{cfg.port}"


def auth_headers(cfg: Config) -> dict:
    return {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}


def health(cfg: Config, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url(cfg)}/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            return {"status": "loading"}
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _log_tail(lines: int = 20) -> str:
    log = paths.logs_dir() / "server.log"
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


_IS_WINDOWS = os.name == "nt"


def _spawn_posix(cmd: list[str], env: dict) -> int:
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(paths.home()), start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc.pid


def _spawn_detached(cmd: list[str], env: dict) -> int:
    proc = subprocess.Popen(
        cmd, env=env, cwd=str(paths.home()),
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc.pid


def _spawn_wmi(cmd: list[str], cwd: str) -> int:
    def quote(text: str) -> str:
        return text.replace("'", "''")

    script = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine='{quote(subprocess.list2cmdline(cmd))}'; CurrentDirectory='{quote(cwd)}'}}; "
        "if ($r.ReturnValue -ne 0) { exit 1 }; $r.ProcessId"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(f"WMI spawn failed: {result.stderr.strip() or result.stdout.strip()}")
    return int(result.stdout.strip().splitlines()[-1])


def ensure_server(cfg: Config, wait_s: float = 300) -> str:
    info = health(cfg)
    if info is not None and info.get("status") == "ok":
        if "voices" not in info:
            raise RuntimeError(f"port {cfg.port} is used by another program")
        return base_url(cfg)

    if info is None or info.get("status") != "loading":
        if netsec.requires_key(cfg.host, cfg.api_key):
            raise RuntimeError(netsec.REFUSE_MESSAGE.format(host=cfg.host))
        paths.home().mkdir(parents=True, exist_ok=True)
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        env = dict(os.environ | config.to_env(cfg))
        env_file = paths.home() / "server.env.json"
        env_file.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        cmd = [
            sys.executable, "-m", "tqd_voicestudio", "serve",
            "--env-file", str(env_file),
            "--log", str(paths.logs_dir() / "server.log"),
        ]
        if _IS_WINDOWS:
            try:
                pid = _spawn_wmi(cmd, str(paths.home()))
                how = "wmi"
            except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
                reason = str(exc).strip().splitlines()[0][:120] if str(exc).strip() else type(exc).__name__
                pid = _spawn_detached(cmd, env)
                how = f"detached (WMI failed: {reason}); server may stop when the MCP session ends"
        else:
            pid = _spawn_posix(cmd, env)
            how = "posix"
        print(f"spawn: {how}", file=sys.stderr)
        (paths.home() / "server.pid").write_text(str(pid), encoding="utf-8")
        print("Đang khởi động TQD-VoiceStudio server (lần đầu có thể mất 1–2 phút)...", file=sys.stderr)

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        info = health(cfg)
        if info is not None and info.get("status") == "ok":
            return base_url(cfg)
        time.sleep(2)
    raise RuntimeError(f"server did not become ready in {wait_s}s\n{_log_tail()}")


def stop_server(cfg: Config) -> bool:
    import psutil

    pidfile = paths.home() / "server.pid"
    if not pidfile.is_file():
        return False
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except ValueError:
        pidfile.unlink(missing_ok=True)
        return False
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(10)
        except psutil.TimeoutExpired:
            proc.kill()
        stopped = True
    except psutil.NoSuchProcess:
        stopped = False
    pidfile.unlink(missing_ok=True)
    return stopped
