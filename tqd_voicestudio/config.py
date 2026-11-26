"""User configuration (flat TOML at paths.config_path())."""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from tqd_voicestudio import paths


@dataclass
class Config:
    port: int = 8765
    device: str = "auto"  # auto | cuda | cpu
    fp16: bool = True
    num_step: int = 16
    max_streams: int = 2
    default_voice: str = "Đinh-Quyết"
    chunker: str = ""  # "" = server default; or legacy | v2
    library_dir: str = ""  # voice pack library (e.g. VIVOS); "" = none
    webhook_secret: str = ""  # HMAC key for job webhooks (generated on first need)
    history_max: int = 500  # newest N outputs kept in history/cache
    jobs_max: int = 100  # newest N terminal jobs kept (older job rows/dirs are pruned)
    host: str = "127.0.0.1"  # bind address; non-loopback requires api_key
    api_key: str = ""  # Bearer token required by every route except /health


def load() -> Config:
    path = paths.config_path()
    if not path.is_file():
        return Config()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    known = {f.name: f for f in fields(Config)}
    values = {}
    for key, value in raw.items():
        if key not in known:
            continue
        values[key] = value
    return Config(**values)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save(cfg: Config) -> Path:
    path = paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k} = {_toml_value(v)}" for k, v in asdict(cfg).items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ensure_webhook_secret() -> str:
    """Return the webhook secret, generating and persisting it on first use."""
    cfg = load()
    if not cfg.webhook_secret:
        cfg.webhook_secret = secrets.token_urlsafe(32)
        save(cfg)
    return cfg.webhook_secret


def generate_api_key() -> str:
    """Create, persist and return a new API key."""
    cfg = load()
    cfg.api_key = secrets.token_urlsafe(32)
    save(cfg)
    return cfg.api_key


def merge_recommended(current: Config, recommended: Config) -> Config:
    """Apply only the doctor-produced fields onto the user's existing config."""
    from dataclasses import replace

    return replace(
        current,
        device=recommended.device,
        fp16=recommended.fp16,
        max_streams=recommended.max_streams,
    )


def to_env(cfg: Config) -> dict[str, str]:
    env = {
        "PORT": str(cfg.port),
        "PORT_HOST": cfg.host,
        "VIZIPVOICE_DEVICE": cfg.device,
        "VIZIPVOICE_FP16": "1" if cfg.fp16 else "0",
        "VIZIPVOICE_NUM_STEP": str(cfg.num_step),
        "VIZIPVOICE_MAX_STREAMS": str(cfg.max_streams),
        "HF_HOME": str(paths.hf_cache()),
        "VIZIPVOICE_MODEL_DIR": str(paths.model_dir()),
        "VIZIPVOICE_VOICES_DIR": str(paths.voices_dir()),
        "PYTHONUTF8": "1",
    }
    if cfg.chunker != "":
        env["TQD_CHUNKER"] = cfg.chunker
    if cfg.library_dir != "":
        env["VIZIPVOICE_LIBRARY_DIR"] = cfg.library_dir
    if cfg.api_key != "":
        env["VIZIPVOICE_API_KEY"] = cfg.api_key
    return env
