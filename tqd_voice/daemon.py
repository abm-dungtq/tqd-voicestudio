"""Start/stop the local TQD-Voice server in the background."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from tqd_voice import config, paths
from tqd_voice.config import Config


def base_url(cfg: Config) -> str:
    return f"http://127.0.0.1:{cfg.port}"


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


def ensure_server(cfg: Config, wait_s: float = 300) -> str:
    info = health(cfg)
    if info is not None and info.get("status") == "ok":
        if "voices" not in info:
            raise RuntimeError(f"port {cfg.port} is used by another program")
        return base_url(cfg)

    paths.home().mkdir(parents=True, exist_ok=True)
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    env = os.environ | config.to_env(cfg)
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with open(paths.logs_dir() / "server.log", "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tqd_voice", "serve"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **kwargs,
        )
    (paths.home() / "server.pid").write_text(str(proc.pid), encoding="utf-8")
    print("Đang khởi động TQD-Voice server (lần đầu có thể mất 1–2 phút)...", file=sys.stderr)

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
