"""Playwright fake-engine server: real FastAPI app, deterministic tone synthesis."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HOME = Path(os.environ.get("TQD_E2E_HOME", str(ROOT / "tests" / "e2e" / ".home")))
if HOME.exists():
    shutil.rmtree(HOME, ignore_errors=True)
HOME.mkdir(parents=True, exist_ok=True)
os.environ["TQD_VOICESTUDIO_HOME"] = str(HOME)
os.environ["VIZIPVOICE_LEXICON_PATH"] = str(HOME / "lexicon.json")
os.environ["VIZIPVOICE_API_KEY"] = "test-key"
os.environ.setdefault("PYTHONUTF8", "1")

import numpy as np
import soundfile as sf
import torch
import uvicorn

import vizipvoice_openai_server as srv

SR = 24000


def tone(seconds: float, freq: float) -> np.ndarray:
    t = np.arange(int(seconds * SR), dtype=np.float32) / SR
    return (0.12 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class FakeEngine:
    max_streams = 2
    active = 0
    waiting = 0

    def __init__(self, *args, **kwargs):
        self.voices = {
            "Alpha": SimpleNamespace(name="Alpha", kind="preset", gender="female", region="Bắc"),
            "Beta": SimpleNamespace(name="Beta", kind="preset", gender="male", region="Nam"),
        }

    def list_voices(self):
        return [
            {
                "id": v.name,
                "name": v.name,
                "kind": v.kind,
                "gender": getattr(v, "gender", None),
                "region": getattr(v, "region", None),
                "description": f"Giọng thử {v.name}",
                "featured": v.name == "Alpha",
                "aliases": [],
                "recommended_speed": 1.0,
            }
            for v in self.voices.values()
        ]

    def resolve_voice(self, name):
        if name in self.voices:
            return self.voices[name]
        raise KeyError(name)

    def acquire(self):
        self.active += 1

    def release(self):
        self.active -= 1

    def synthesize_tensor(self, text, entry, speed=None, style="auto", quality_check=None,
                          segments_out=None, qc_out=None):
        time.sleep(0.15)
        digest = int(hashlib.md5(entry.name.encode("utf-8")).hexdigest(), 16)
        seconds = max(0.65, min(2.0, 0.22 * len(text.split())))
        wav = tone(seconds, 220 + (digest % 5) * 55)
        if segments_out is not None:
            segments_out.append({"start": 0.0, "end": round(len(wav) / SR, 3), "text": text})
        if quality_check and qc_out is not None:
            qc_out["ran"] = True
            qc_out["max_cer"] = 0.01
            qc_out["retries"] = 0
        return torch.from_numpy(wav).unsqueeze(0)

    def enroll_voice(self, name, wav_bytes, prompt_text, description, force, gender, region, auto_transcribe):
        if name in self.voices and not force:
            raise FileExistsError(name)
        out = srv.CUSTOM_VOICES_DIR / f"{name}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(wav_bytes)
        entry = SimpleNamespace(
            name=name,
            kind="custom",
            gender=gender or None,
            region=region or None,
            wav_path=out,
            prompt_text=prompt_text or "lời thử",
            transcript_source="user" if prompt_text else "asr",
            description=description or "Giọng clone thử",
        )
        self.voices[name] = entry
        return entry

    def delete_voice(self, name):
        entry = self.resolve_voice(name)
        if entry.kind != "custom":
            raise PermissionError("không xóa được giọng studio có sẵn")
        Path(entry.wav_path).unlink(missing_ok=True)
        self.voices.pop(name, None)
        return entry


async def _noop_async():
    return None


# Keep the MCP subsystem out of the model-free E2E server; all HTTP routes under test are real.
srv.ViZipVoiceEngine = FakeEngine
srv.mcp_gateway.start = _noop_async
srv.mcp_gateway.stop = _noop_async

# Seed one music track so the Đọc tab can exercise the music payload controls.
music_dir = srv._music_dir()
music_path = music_dir / "nhac-thu.wav"
sf.write(str(music_path), tone(3.0, 110.0), SR, subtype="PCM_16")
srv.get_store().add_music("Nhạc thử", 3.0, str(music_path))


def main():
    try:
        uvicorn.run(srv.app, host="127.0.0.1", port=8013, log_level="warning")
    finally:
        if srv._JOBS is not None:
            srv._JOBS.stop()
        if srv._STORE is not None:
            srv._STORE.close()
        shutil.rmtree(HOME, ignore_errors=True)


if __name__ == "__main__":
    main()
