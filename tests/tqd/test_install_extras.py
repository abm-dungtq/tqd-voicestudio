import sys
from argparse import Namespace
from dataclasses import replace

import huggingface_hub
import pytest

from tqd_voicestudio import config, install
from tqd_voicestudio.doctor import Facts


def _facts():
    return Facts(
        os="Windows", arch="AMD64", python="3.12.0", torch_version="2.8.0", torch_cuda="12.8",
        cuda_available=True, gpu_name="RTX 3060", vram_total_gb=12.0, nvidia_smi=True,
        driver_version="570.0", ram_total_gb=32.0, ram_avail_gb=20.0, disk_free_gb=100.0,
        ffmpeg="ffmpeg", port=8765, port_state="free", model_present=True,
    )


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    for name in ("HF_HOME", "VIZIPVOICE_MODEL_DIR", "VIZIPVOICE_VOICES_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(install.doctor, "collect", lambda cfg: replace(_facts()))
    calls = []

    def fake_download(repo_id, **kw):
        calls.append((repo_id, kw))
        return str(kw.get("local_dir", ""))

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)
    return tmp_path, calls


def _args(**kw):
    base = dict(no_asr=True, no_smoke=True, voices=None, yes=False)
    base.update(kw)
    return Namespace(**base)


def test_plain_install_downloads_base_only(env):
    _, calls = env
    assert install.run(_args()) == 0
    repos = [c[0] for c in calls]
    assert install.MODEL_REPO in repos and install.VIVOS_REPO not in repos


def test_extras_need_consent_when_not_interactive(env, monkeypatch):
    _, calls = env
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert install.run(_args(voices="vivos")) == 2
    assert calls == []


def test_vivos_with_yes_skips_base_and_sets_library_dir(env):
    home, calls = env
    (home / "models" / "ViZipvoice").mkdir(parents=True)
    (home / "models" / "ViZipvoice" / "checkpoint-1860000.pt").write_bytes(b"x")
    assert install.run(_args(voices="vivos", yes=True)) == 0
    repos = [c[0] for c in calls]
    assert install.MODEL_REPO not in repos
    assert install.VIVOS_REPO in repos
    vivos_kw = dict(calls)[install.VIVOS_REPO]
    assert vivos_kw["repo_type"] == "dataset"
    assert config.load().library_dir == str(home / "packs" / "vivos" / "library")


def test_interactive_decline_cancels(env, monkeypatch):
    _, calls = env
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert install.run(_args(voices="vivos")) == 2
    assert calls == []


def test_config_library_dir_maps_to_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    assert "VIZIPVOICE_LIBRARY_DIR" not in config.to_env(config.Config())
    env = config.to_env(config.Config(library_dir="X:/lib"))
    assert env["VIZIPVOICE_LIBRARY_DIR"] == "X:/lib"
