from tqd_voicestudio import config
from tqd_voicestudio.config import Config


def test_save_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    config.save(Config(port=9999, device="cpu", fp16=False, chunker="v2"))
    cfg = config.load()
    assert (cfg.port, cfg.device, cfg.fp16, cfg.chunker) == (9999, "cpu", False, "v2")
    assert cfg.default_voice == "Đinh-Quyết"


def test_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    env = config.to_env(Config(device="cpu", fp16=False))
    assert env["VIZIPVOICE_FP16"] == "0"
    assert "TQD_CHUNKER" not in env
    assert env["PORT_HOST"] == "127.0.0.1"


def test_unknown_keys_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('port = 1234\nbogus = "x"\n', encoding="utf-8")
    assert config.load().port == 1234
