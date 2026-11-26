from pathlib import Path

from tqd_voicestudio import paths

ENVS = (
    "TQD_VOICESTUDIO_HOME",
    "VIZIPVOICE_MODEL_DIR",
    "VIZIPVOICE_VOICES_DIR",
    "HF_HOME",
    "VIZIPVOICE_CT2_MODEL",
)


def _clear(monkeypatch):
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)


def test_home_env_drives_defaults(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    assert paths.model_dir() == tmp_path / "models" / "ViZipvoice"
    assert paths.voices_dir() == tmp_path / "voices"
    assert paths.hf_cache() == tmp_path / "cache" / "hf"
    assert paths.ct2_asr_dir() == tmp_path / "models" / "phowhisper-small-ct2"
    assert paths.outputs_dir() == tmp_path / "outputs"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.config_path() == tmp_path / "config.toml"


def test_specific_env_wins(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIZIPVOICE_MODEL_DIR", str(tmp_path / "m"))
    monkeypatch.setenv("VIZIPVOICE_VOICES_DIR", str(tmp_path / "v"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setenv("VIZIPVOICE_CT2_MODEL", str(tmp_path / "ct2"))
    assert paths.model_dir() == tmp_path / "m"
    assert paths.voices_dir() == tmp_path / "v"
    assert paths.hf_cache() == tmp_path / "hf"
    assert paths.ct2_asr_dir() == tmp_path / "ct2"


def test_default_home(monkeypatch):
    _clear(monkeypatch)
    assert paths.home() == Path.home() / ".tqd-voicestudio"


def test_db_path(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    assert paths.db_path() == tmp_path / "studio.db"
