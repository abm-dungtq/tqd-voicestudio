"""Encode mono PCM16 bytes to wav/mp3/opus/flac via ffmpeg pipes."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import threading
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


MAX_DECODE_DURATION_S = 600.0
# Safety ceiling requested for the ffmpeg fallback: roughly 600 s of stereo
# 48 kHz 16-bit audio, rounded to the review-specified ~230 MiB budget.
MAX_FFMPEG_DECODE_BYTES = 230 * 1024 * 1024
_DURATION_ERROR = "Âm thanh vượt quá giới hạn 10 phút (600 giây)."


def _reject_over_duration() -> None:
    raise ValueError(_DURATION_ERROR)


def _check_decoded_duration(wav, sr: int):
    duration = len(wav) / float(max(int(sr), 1))
    if duration > MAX_DECODE_DURATION_S:
        _reject_over_duration()


def _probe_duration_ffprobe(path: str) -> float | None:
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        proc = subprocess.run(
            [
                probe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True,
            timeout=20,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        duration = float(proc.stdout.decode("ascii", "ignore").strip())
    except ValueError:
        return None
    return duration if duration == duration else None  # reject NaN


def _decode_ffmpeg_capped(exe: str, input_path: str) -> bytes:
    """Run ffmpeg decode while capping stdout, avoiding unbounded decode bombs."""
    proc = subprocess.Popen(
        [exe, "-v", "error", "-i", input_path, "-f", "wav", "-ac", "1", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr_parts = []

    def drain_stderr() -> None:
        try:
            stderr_parts.append(proc.stderr.read() if proc.stderr else b"")
        except Exception:
            stderr_parts.append(b"")

    drain = threading.Thread(target=drain_stderr, daemon=True)
    drain.start()
    output = bytearray()
    overflow = False
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(1024 * 1024)
        if not chunk:
            break
        if len(output) + len(chunk) > MAX_FFMPEG_DECODE_BYTES:
            overflow = True
            proc.kill()
            break
        output.extend(chunk)
    proc.wait()
    drain.join(timeout=2)
    if overflow:
        _reject_over_duration()
    stderr = b"".join(stderr_parts).decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not output:
        detail = f": {stderr[:300]}" if stderr else ""
        raise ValueError(f"không giải mã được file âm thanh{detail}")
    return bytes(output)


def decode_audio(data: bytes):
    """Decode arbitrary audio bytes to (mono float32 ndarray, sample_rate).

    Tries soundfile first, then ffmpeg. The 10-minute ceiling is enforced from
    container metadata before decoding whenever possible; the ffmpeg fallback is
    additionally stdout-capped so a hostile stream cannot cause an unbounded decode.
    """
    import numpy as np
    import soundfile as sf

    # soundfile parses container metadata without allocating/decoding samples.
    # Checking frames/samplerate here rejects oversized WAV inputs before sf.read
    # can allocate the full float32 array. FLAC uses SF_COUNT_MAX when the encoder
    # left the total frame count unknown; that sentinel is not a duration claim and
    # must go through the probed/capped ffmpeg path instead of sf.read (which would
    # otherwise try to allocate the sentinel-sized array).
    soundfile_readable = True
    sf_count_max = (1 << 63) - 1
    try:
        info = sf.info(io.BytesIO(data), verbose=False)
        if info.frames == sf_count_max:
            soundfile_readable = False
        elif info.samplerate and info.frames / float(info.samplerate) > MAX_DECODE_DURATION_S:
            _reject_over_duration()
    except ValueError as exc:
        if str(exc) == _DURATION_ERROR:
            raise
    except Exception:
        pass

    if soundfile_readable:
        try:
            wav, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        except Exception:
            pass
        else:
            mono = wav.mean(axis=1).astype(np.float32)
            _check_decoded_duration(mono, int(sr))
            return mono, int(sr)

    exe = shutil.which(FFMPEG)
    if not exe:
        raise ValueError("không giải mã được file âm thanh (và không có ffmpeg để thử lại)")

    # ffmpeg/ffprobe can read pipes, but a temporary seekable input makes probing
    # substantially more reliable and also removes stdin/stdout pipe deadlocks.
    fd, input_path = tempfile.mkstemp(prefix="tqd-decode-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        duration = _probe_duration_ffprobe(input_path)
        if duration is not None and duration > MAX_DECODE_DURATION_S:
            _reject_over_duration()
        decoded = _decode_ffmpeg_capped(exe, input_path)
    finally:
        try:
            os.unlink(input_path)
        except OSError:
            pass

    try:
        wav, sr = sf.read(io.BytesIO(decoded), dtype="float32", always_2d=True)
    except Exception as exc:
        raise ValueError("không giải mã được file âm thanh") from exc
    mono = wav.mean(axis=1).astype(np.float32)
    _check_decoded_duration(mono, int(sr))
    return mono, int(sr)
