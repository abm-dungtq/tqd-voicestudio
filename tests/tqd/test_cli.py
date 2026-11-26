import socket

import pytest

from tqd_voice import daemon
from tqd_voice.cli import main
from tqd_voice.config import Config


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_health_none_on_closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert daemon.health(Config(port=port)) is None


def test_say_without_text_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["say"])
    assert exc.value.code == 2


def test_ensure_server_waits_for_loading_server(monkeypatch):
    states = iter([{"status": "loading"}, {"status": "loading"}, {"status": "ok", "voices": 3}])
    monkeypatch.setattr(daemon, "health", lambda cfg, timeout=1.0: next(states))
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)

    def boom(*a, **k):
        raise AssertionError("Popen must not be called while a server is loading")

    monkeypatch.setattr(daemon.subprocess, "Popen", boom)
    cfg = Config(port=8765)
    assert daemon.ensure_server(cfg, wait_s=30) == "http://127.0.0.1:8765"


def _windows_ensure(monkeypatch, tmp_path, wmi, detached):
    monkeypatch.setenv("TQD_VOICE_HOME", str(tmp_path))
    monkeypatch.setattr(daemon, "_IS_WINDOWS", True)
    states = iter([None, {"status": "ok", "voices": 1}])
    monkeypatch.setattr(daemon, "health", lambda cfg, timeout=1.0: next(states))
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    monkeypatch.setattr(daemon, "_spawn_wmi", wmi)
    monkeypatch.setattr(daemon, "_spawn_detached", detached)
    cfg = Config(port=8765, device="cpu", fp16=False)
    return daemon.ensure_server(cfg, wait_s=30)


def test_windows_uses_wmi_and_writes_pid(monkeypatch, tmp_path):
    import json

    def no_detached(cmd, env):
        raise AssertionError("detached must not be used when WMI works")

    url = _windows_ensure(monkeypatch, tmp_path, lambda cmd, cwd: 4242, no_detached)
    assert url == "http://127.0.0.1:8765"
    assert (tmp_path / "server.pid").read_text(encoding="utf-8") == "4242"
    env = json.loads((tmp_path / "server.env.json").read_text(encoding="utf-8"))
    assert env["VIZIPVOICE_DEVICE"] == "cpu" and env["VIZIPVOICE_FP16"] == "0"
    assert env["PORT"] == "8765"


def test_windows_falls_back_to_detached_when_wmi_fails(monkeypatch, tmp_path, capsys):
    def wmi_fail(cmd, cwd):
        raise RuntimeError("WMI spawn failed: denied")

    _windows_ensure(monkeypatch, tmp_path, wmi_fail, lambda cmd, env: 777)
    assert (tmp_path / "server.pid").read_text(encoding="utf-8") == "777"
    assert "spawn: detached (WMI failed: WMI spawn failed: denied)" in capsys.readouterr().err


def test_serve_env_file_is_loaded(monkeypatch, tmp_path):
    import json

    from tqd_voice import cli

    f = tmp_path / "env.json"
    f.write_text(json.dumps({"TQD_TEST_ENV_KEY": "giá trị"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv("TQD_TEST_ENV_KEY", raising=False)
    cli._load_env_file(f)
    import os

    assert os.environ["TQD_TEST_ENV_KEY"] == "giá trị"
    monkeypatch.delenv("TQD_TEST_ENV_KEY")


def test_env_file_prepends_pythonpath_to_sys_path(monkeypatch, tmp_path):
    import json
    import os
    import sys

    from tqd_voice import cli

    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    f = tmp_path / "env.json"
    f.write_text(json.dumps({"PYTHONPATH": os.pathsep.join([a, "", b, a])}), encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("PYTHONPATH", "")
    cli._load_env_file(f)
    assert sys.path[:2] == [a, b]
    assert sys.path.count(a) == 1
