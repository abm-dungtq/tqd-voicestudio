"""Pure numpy audio post-processing: loudness, silence trimming, music mixing with ducking."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

Cut = Tuple[int, int]  # (position in the original samples, samples removed)


def _soft_limit(y: np.ndarray, ceiling: float, knee: float = 0.5) -> np.ndarray:
    """Smooth peak limiter: identity below ``knee*ceiling``, tanh-compressed above, never above ``ceiling``."""
    t = knee * ceiling
    mag = np.abs(y)
    over = mag > t
    out = y.copy()
    out[over] = np.sign(y[over]) * (t + (ceiling - t) * np.tanh((mag[over] - t) / (ceiling - t)))
    return out


def normalize_loudness(wav: np.ndarray, sr: int, target_lufs: float = -16.0, peak_db: float = -1.0) -> np.ndarray:
    """Integrated loudness to ``target_lufs`` (BS.1770) with a soft peak limit at ``peak_db`` dBFS.

    A pure gain would miss the target on peaky material (the peak ceiling wins), so the gain is
    refined over a few iterations while the soft limiter holds the ceiling.
    """
    import pyloudnorm

    x = np.asarray(wav, dtype=np.float64).reshape(-1)
    if x.size < int(0.4 * sr) or float(np.max(np.abs(x))) < 1e-6:
        return np.asarray(wav, dtype=np.float32)
    meter = pyloudnorm.Meter(sr)
    loudness = meter.integrated_loudness(x)
    if not np.isfinite(loudness):
        return np.asarray(wav, dtype=np.float32)
    ceiling = 10.0 ** (peak_db / 20.0)
    gain_db = target_lufs - loudness
    y = x
    for _ in range(6):
        y = _soft_limit(x * (10.0 ** (gain_db / 20.0)), ceiling)
        got = meter.integrated_loudness(y)
        if not np.isfinite(got) or abs(got - target_lufs) < 0.1:
            break
        gain_db += target_lufs - got
    return np.clip(y, -ceiling, ceiling).astype(np.float32)


def _frame_rms_db(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.array([], dtype=np.float64)
    f = x[: n * frame].reshape(n, frame).astype(np.float64)
    rms = np.sqrt(np.mean(f * f, axis=1) + 1e-12)
    return 20.0 * np.log10(rms)


def trim_silences(
    wav: np.ndarray,
    sr: int,
    protected: Sequence[Cut] = (),
    max_silence_s: float = 0.6,
    threshold_db: float = -50.0,
) -> Tuple[np.ndarray, List[Cut]]:
    """Shorten silences longer than ``max_silence_s`` to ``max_silence_s``.

    ``protected`` are (start, end) sample ranges (explicit pauses) that are never touched.
    Returns the new audio and ``cuts`` = [(old_position_where_removal_starts, removed_samples)].
    """
    x = np.asarray(wav, dtype=np.float32).reshape(-1)
    frame = max(int(0.02 * sr), 1)
    db = _frame_rms_db(x, frame)
    silent = db < threshold_db
    keep_len = int(max_silence_s * sr)
    cuts: List[Cut] = []
    pieces: List[np.ndarray] = []
    pos = 0
    i = 0
    n = len(silent)
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < n and silent[j]:
            j += 1
        s, e = i * frame, j * frame
        if j == n:
            e = len(x)
        i = j
        if e - s <= keep_len:
            continue
        # carve out protected overlap: only trim the unprotected sub-ranges
        sub = [(s, e)]
        for ps, pe in protected:
            nxt = []
            for a, b in sub:
                if pe <= a or ps >= b:
                    nxt.append((a, b))
                    continue
                if ps > a:
                    nxt.append((a, ps))
                if pe < b:
                    nxt.append((pe, b))
            sub = nxt
        for a, b in sub:
            if b - a > keep_len:
                # keep the first ``keep_len`` samples of the silence, drop the rest
                pieces.append(x[pos : a + keep_len])
                cuts.append((a + keep_len, (b - a) - keep_len))
                pos = b
    pieces.append(x[pos:])
    return np.concatenate(pieces) if pieces else x, cuts


def remap_time(t_seconds: float, cuts: Sequence[Cut], sr: int) -> float:
    """Map a time in the original audio to the trimmed audio."""
    pos = t_seconds * sr
    removed = 0
    for at, size in cuts:
        if pos >= at + size:
            removed += size
        elif pos > at:
            removed += pos - at
        else:
            break
    return round((pos - removed) / sr, 3)


def remap_ranges(ranges: Sequence[Cut], cuts: Sequence[Cut]) -> List[Cut]:
    out = []
    for a, b in ranges:
        ra, rb = a, b
        for at, size in cuts:
            if a >= at + size:
                ra -= size
            if b >= at + size:
                rb -= size
        out.append((ra, rb))
    return out


def speech_envelope_active(speech: np.ndarray, sr: int, threshold_db: float = -50.0, frame_s: float = 0.02) -> np.ndarray:
    frame = max(int(frame_s * sr), 1)
    db = _frame_rms_db(np.asarray(speech, dtype=np.float32).reshape(-1), frame)
    return db > threshold_db


def mix_music(
    speech: np.ndarray,
    music: np.ndarray,
    sr: int,
    volume_db: float = -20.0,
    duck_db: float = -12.0,
    fade_s: float = 2.0,
    attack_s: float = 0.05,
    release_s: float = 0.3,
) -> np.ndarray:
    """Mix looped music under speech: music level ``volume_db`` relative to speech loudness,
    ducked by ``duck_db`` (negative dB) while speech is active, fade-out over the last ``fade_s``.

    Output length equals the speech length.
    """
    sp = np.asarray(speech, dtype=np.float32).reshape(-1)
    mu = np.asarray(music, dtype=np.float32).reshape(-1)
    if len(mu) == 0 or len(sp) == 0:
        return sp
    reps = int(np.ceil(len(sp) / len(mu)))
    mu = np.tile(mu, reps)[: len(sp)].copy()

    sp_rms = float(np.sqrt(np.mean(sp.astype(np.float64) ** 2) + 1e-12))
    mu_rms = float(np.sqrt(np.mean(mu.astype(np.float64) ** 2) + 1e-12))
    mu *= (sp_rms / mu_rms) * (10.0 ** (volume_db / 20.0))

    frame = max(int(0.01 * sr), 1)
    n_frames = int(np.ceil(len(sp) / frame))
    active = speech_envelope_active(sp, sr, frame_s=0.01)
    active = np.concatenate([active, np.zeros(max(n_frames - len(active), 0), dtype=bool)])[:n_frames]
    target = np.where(active, 10.0 ** (-abs(duck_db) / 20.0), 1.0)
    gain = np.empty(n_frames, dtype=np.float64)
    a_att = np.exp(-0.01 / max(attack_s, 1e-4))
    a_rel = np.exp(-0.01 / max(release_s, 1e-4))
    g = 1.0
    for i, tg in enumerate(target):
        coef = a_att if tg < g else a_rel
        g = coef * g + (1.0 - coef) * tg
        gain[i] = g
    gain_s = np.repeat(gain, frame)[: len(sp)]
    mu *= gain_s.astype(np.float32)

    fade = int(min(fade_s * sr, len(mu)))
    if fade > 0:
        mu[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    out = sp + mu
    peak = float(np.max(np.abs(out)))
    if peak > 1.0:
        out = out / peak
    return out.astype(np.float32)
