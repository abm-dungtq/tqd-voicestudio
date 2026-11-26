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
