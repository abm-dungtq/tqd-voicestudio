"""Voice-name metadata helpers for the ``vi-<region>-<gender>-<name>-<style>-vN``
custom-voice convention plus sidecar JSON and voice filtering."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np

VOICE_NAME_RE = re.compile(r"^vi-(bac|trung|nam)-(nam|nu)-([a-z0-9]+)-([a-z0-9]+)-v\d+$")

_REGION_TOKENS = {"bac": "Bắc", "trung": "Trung", "nam": "Nam"}
_GENDER_TOKENS = {"nam": "male", "nu": "female"}

_REGION_ALIASES = {
    "bac": "Bắc",
    "north": "Bắc",
    "northern": "Bắc",
    "trung": "Trung",
    "central": "Trung",
    "nam": "Nam",
    "south": "Nam",
    "southern": "Nam",
}

_GENDER_ALIASES = {
    "male": "male",
    "m": "male",
    "nam": "male",
    "female": "female",
    "f": "female",
    "nu": "female",
}


def strip_diacritics(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    out = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return out.replace("đ", "d").replace("Đ", "D")


def parse_voice_name(name: str) -> Optional[dict]:
    """Parse ``vi-bac-nam-ducanh-tunhien-v1`` style names.

    Returns ``{"region": "Bắc"|"Trung"|"Nam", "gender": "male"|"female",
    "name": ..., "style": ...}`` or ``None`` when the name does not match.
    """
    m = VOICE_NAME_RE.match(unicodedata.normalize("NFC", (name or "").strip()))
    if not m:
        return None
    region_tok, gender_tok, person, style = m.groups()
    return {
        "region": _REGION_TOKENS[region_tok],
        "gender": _GENDER_TOKENS[gender_tok],
        "name": person,
        "style": style,
    }


def normalize_region(s: Optional[str]) -> Optional[str]:
    """Map bac/bắc/north/trung/central/nam/south (any case/diacritics) to a
    canonical region label, or ``None`` when unrecognised."""
    if not s:
        return None
    key = strip_diacritics(str(s)).strip().lower()
    return _REGION_ALIASES.get(key)


def normalize_gender(s: Optional[str]) -> Optional[str]:
    """Map male/female/nam/nữ/nu (any case/diacritics) to canonical gender."""
    if not s:
        return None
    key = strip_diacritics(str(s)).strip().lower()
    return _GENDER_ALIASES.get(key)


def read_sidecar(path) -> dict:
    """Read ``voices/<name>.json``; returns ``{}`` when absent/invalid."""
    p = Path(path)
    try:
        if not p.is_file():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_sidecar(path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(p)


_F0_MIN_HZ = 60.0
_F0_MAX_HZ = 400.0
_F0_FRAME_S = 0.040
_F0_HOP_S = 0.010
_F0_THRESHOLD = 0.1
_F0_MIN_VOICED_FRAMES = 10


def _yin_frame_f0(frame: np.ndarray, sr: int, tau_min: int, tau_max: int) -> Optional[float]:
    """YIN fundamental-frequency estimate for a single frame, or ``None``."""
    n = frame.shape[0]
    taus = np.arange(tau_min, min(tau_max, n - 2) + 1)
    if taus.size == 0:
        return None
    diff = np.empty(taus.shape[0], dtype=np.float64)
    for i, tau in enumerate(taus):
        d = frame[: n - tau] - frame[tau:]
        diff[i] = float(d @ d)
    # Cumulative mean normalised difference function.
    cumsum = np.cumsum(diff)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmd = np.where(cumsum > 0, diff * taus / cumsum, 1.0)
    below = np.flatnonzero(cmd < _F0_THRESHOLD)
    if below.size == 0:
        return None
    first = int(below[0])
    # Local minimum within the dip.
    end = first
    while end + 1 < cmd.shape[0] and cmd[end + 1] < cmd[end]:
        end += 1
    tau = taus[first:end + 1][int(np.argmin(cmd[first:end + 1]))]
    idx = int(np.flatnonzero(taus == tau)[0])
    # Parabolic interpolation around the minimum.
    if 0 < idx < cmd.shape[0] - 1:
        y0, y1, y2 = cmd[idx - 1], cmd[idx], cmd[idx + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            shift = 0.5 * (y0 - y2) / denom
            if abs(shift) <= 1.0:
                tau = tau + shift
    if tau <= 0:
        return None
    return float(sr / tau)


def estimate_median_f0(wav, sr: int) -> Optional[float]:
    """Median F0 in Hz over voiced frames (numpy YIN, 60-400 Hz), or
    ``None`` when fewer than 10 voiced frames are detected."""
    arr = wav
    if not isinstance(arr, np.ndarray):
        arr = arr.detach().float().cpu().numpy()
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr.mean(axis=0) if arr.shape[0] > 1 else arr[0]
    arr = arr.reshape(-1)
    if arr.size == 0 or float(np.abs(arr).max()) < 1e-4:
        return None
    frame_len = max(int(round(_F0_FRAME_S * sr)), 8)
    hop = max(int(round(_F0_HOP_S * sr)), 1)
    tau_min = max(int(sr / _F0_MAX_HZ), 2)
    tau_max = max(int(sr / _F0_MIN_HZ), tau_min + 1)
    n_frames = 1 + (arr.size - frame_len) // hop
    if n_frames <= 0:
        return None
    starts = np.arange(n_frames) * hop
    frames = np.stack([arr[s : s + frame_len] for s in starts])
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    energy_ok = rms > float(rms.max()) * 0.1
    f0s = []
    for i in np.flatnonzero(energy_ok):
        f0 = _yin_frame_f0(frames[i], sr, tau_min, tau_max)
        if f0 is not None and _F0_MIN_HZ <= f0 <= _F0_MAX_HZ:
            f0s.append(f0)
    if len(f0s) < _F0_MIN_VOICED_FRAMES:
        return None
    return float(np.median(f0s))


def filter_voices(
    voices: list,
    gender: Optional[str] = None,
    region: Optional[str] = None,
    kind: Optional[str] = None,
    q: Optional[str] = None,
) -> list:
    """Filter a list of voice dicts by gender/region/kind and free-text ``q``.

    ``q`` is a case-insensitive substring match over id/name/description/aliases.
    """
    g = normalize_gender(gender)
    r = normalize_region(region)
    query = unicodedata.normalize("NFC", q).lower() if q else None

    def _haystack(v: dict) -> str:
        parts = [
            str(v.get("id") or ""),
            str(v.get("name") or ""),
            str(v.get("description") or ""),
            " ".join(str(a) for a in (v.get("aliases") or [])),
        ]
        return unicodedata.normalize("NFC", " ".join(parts)).lower()

    out = []
    for v in voices:
        if g is not None and normalize_gender(v.get("gender")) != g:
            continue
        if r is not None and normalize_region(v.get("region")) != r:
            continue
        if kind and str(v.get("kind") or "") != kind:
            continue
        if query and query not in _haystack(v):
            continue
        out.append(v)
    return out
