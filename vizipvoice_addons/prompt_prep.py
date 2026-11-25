"""Pick a clean speech window out of a longer reference clip for TTS prompts."""

from __future__ import annotations

from typing import Tuple

import numpy as np

FRAME_S = 0.020  # 20 ms analysis frames
SILENCE_DROP_DB = 35.0  # frames below (peak_db - 35) count as silence
MIN_SILENCE_RUN_S = 0.150  # silence runs this long are cut points


def _frame_db(wav: np.ndarray, frame_len: int) -> np.ndarray:
    n_frames = wav.size // frame_len
    if n_frames == 0:
        return np.full(1, -120.0, dtype=np.float64)
    frames = wav[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    return 20.0 * np.log10(rms + 1e-12)


def _trim_edge_silence_bounds(wav: np.ndarray, sr: int) -> Tuple[int, int]:
    """(start, end) sample bounds with leading/trailing silent frames removed."""
    frame_len = max(int(sr * FRAME_S), 1)
    db = _frame_db(wav, frame_len)
    thresh = float(np.max(db)) - SILENCE_DROP_DB
    voiced = np.nonzero(db >= thresh)[0]
    if voiced.size == 0:
        return 0, wav.size
    start = int(voiced[0]) * frame_len
    end = min(wav.size, (int(voiced[-1]) + 1) * frame_len)
    return start, end


def select_prompt_segment(
    wav: np.ndarray, sr: int, min_s: float = 5.0, max_s: float = 10.0
) -> Tuple[int, int]:
    """Return (start_sample, end_sample) of the best speech window.

    Silence = 20 ms frames whose RMS dB is more than 35 dB under the peak.
    Cut points = silence runs >= 150 ms. Among the windows between consecutive
    cut points (plus clip edges) with duration in [min_s, max_s], pick the one
    with the highest fraction of speech frames. Fallback: first ``max_s``
    seconds with edge silence trimmed. Clips <= max_s return the full range.
    """
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    total = wav.size
    if total == 0:
        return 0, 0
    if total <= int(max_s * sr):
        return 0, total

    frame_len = max(int(sr * FRAME_S), 1)
    n_frames = total // frame_len
    db = _frame_db(wav, frame_len)
    peak_db = float(np.max(db))
    is_silence = db < (peak_db - SILENCE_DROP_DB)

    # Silence runs long enough to cut at: list of (start_frame, end_frame)
    min_run = max(int(round(MIN_SILENCE_RUN_S / FRAME_S)), 1)
    runs = []
    i = 0
    while i < n_frames:
        if is_silence[i]:
            j = i
            while j < n_frames and is_silence[j]:
                j += 1
            if j - i >= min_run:
                runs.append((i, j))
            i = j
        else:
            i += 1

    # Candidate windows: between end of one run and start of the next,
    # plus [0, first_run_start) and [last_run_end, n_frames).
    bounds = []
    prev_end = 0
    for rs, re_ in runs:
        bounds.append((prev_end, rs))
        prev_end = re_
    bounds.append((prev_end, n_frames))

    min_frames = int(min_s / FRAME_S)
    max_frames = int(max_s / FRAME_S)
    best = None  # (speech_ratio, start_sample, end_sample)
    for fs, fe in bounds:
        length = fe - fs
        if length < min_frames or length > max_frames:
            continue
        speech = float(np.count_nonzero(~is_silence[fs:fe]))
        ratio = speech / float(length)
        cand = (ratio, fs * frame_len, min(total, fe * frame_len))
        if best is None or cand[0] > best[0]:
            best = cand

    if best is not None:
        return int(best[1]), int(best[2])

    # Fallback: first max_s seconds with edge silence trimmed
    limit = min(total, int(max_s * sr))
    ts, te = _trim_edge_silence_bounds(wav[:limit], sr)
    if te - ts < int(min_s * sr):
        return 0, limit
    return int(ts), int(te)
