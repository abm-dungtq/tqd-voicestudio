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
