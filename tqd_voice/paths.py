"""Single source of truth for TQD-Voice data directories (paths only, never created here)."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("TQD_VOICE_HOME")
    base = Path(env) if env else Path.home() / ".tqd-voice"
    return base.expanduser()


def model_dir() -> Path:
    env = os.environ.get("VIZIPVOICE_MODEL_DIR")
    return Path(env).expanduser() if env else home() / "models" / "ViZipvoice"


def voices_dir() -> Path:
    env = os.environ.get("VIZIPVOICE_VOICES_DIR")
    return Path(env).expanduser() if env else home() / "voices"


def hf_cache() -> Path:
    env = os.environ.get("HF_HOME")
    return Path(env).expanduser() if env else home() / "cache" / "hf"


def ct2_asr_dir() -> Path:
    env = os.environ.get("VIZIPVOICE_CT2_MODEL")
    return Path(env).expanduser() if env else home() / "models" / "phowhisper-small-ct2"


def outputs_dir() -> Path:
    return home() / "outputs"


def logs_dir() -> Path:
    return home() / "logs"


def config_path() -> Path:
    return home() / "config.toml"
