"""Encode mono PCM16 bytes to wav/mp3/opus/flac via ffmpeg pipes."""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from typing import Tuple

FFMPEG = "ffmpeg"

_FORMATS = ("pcm", "wav", "mp3", "opus", "flac")


def _require_ffmpeg() -> str:
    exe = shutil.which(FFMPEG)
    if not exe:
        raise RuntimeError(
            "ffmpeg not found on PATH; install ffmpeg to use mp3/opus/flac output"
        )
    return exe


def _pcm_to_wav(pcm_bytes: bytes, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def _ffmpeg_encode(pcm_bytes: bytes, sr: int, args: list) -> bytes:
    exe = _require_ffmpeg()
    cmd = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(sr),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        *args,
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=pcm_bytes, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg encode failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:400]}"
        )
    return proc.stdout


def encode_pcm16(pcm_bytes: bytes, sr: int, fmt: str) -> Tuple[bytes, str]:
    """Return ``(encoded_bytes, media_type)`` for fmt in pcm/wav/mp3/opus/flac."""
    fmt = (fmt or "").lower()
    if fmt == "pcm":
        return pcm_bytes, f"audio/pcm;rate={sr};encoding=signed-int;bits=16"
    if fmt == "wav":
        return _pcm_to_wav(pcm_bytes, sr), "audio/wav"
    if fmt == "mp3":
        data = _ffmpeg_encode(
            pcm_bytes, sr, ["-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]
        )
        return data, "audio/mpeg"
    if fmt == "opus":
        data = _ffmpeg_encode(
            pcm_bytes, sr, ["-codec:a", "libopus", "-b:a", "64k", "-f", "ogg"]
        )
        return data, "audio/ogg"
    if fmt == "flac":
        data = _ffmpeg_encode(pcm_bytes, sr, ["-codec:a", "flac", "-f", "flac"])
        return data, "audio/flac"
    raise ValueError(f"unsupported audio format {fmt!r}; expected one of {_FORMATS}")


def decode_audio(data: bytes):
    """Decode arbitrary audio bytes to (mono float32 ndarray, sample_rate).

    Tries soundfile first, then an ffmpeg pipe. Raises ValueError when undecodable.
    """
    import numpy as np
    import soundfile as sf

    try:
        wav, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        return wav.mean(axis=1).astype(np.float32), int(sr)
    except Exception:
        pass
    exe = shutil.which(FFMPEG)
    if not exe:
        raise ValueError("không giải mã được file âm thanh (và không có ffmpeg để thử lại)")
    proc = subprocess.run(
        [exe, "-v", "error", "-i", "pipe:0", "-f", "wav", "-ac", "1", "pipe:1"],
        input=data, capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ValueError("không giải mã được file âm thanh")
    try:
        wav, sr = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=True)
    except Exception as exc:
        raise ValueError("không giải mã được file âm thanh") from exc
    return wav.mean(axis=1).astype(np.float32), int(sr)
