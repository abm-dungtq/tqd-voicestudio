import numpy as np

from vizipvoice_addons.prompt_prep import select_prompt_segment

SR = 24000


def _tone(dur_s, freq=220.0, amp=0.3, sr=SR):
    t = np.arange(int(dur_s * sr), dtype=np.float32) / sr
    return amp * np.sin(2 * np.pi * freq * t)


def _silence(dur_s, sr=SR):
    return np.zeros(int(dur_s * sr), dtype=np.float32)


def test_short_clip_returns_full_range():
    wav = _tone(8.0)
    assert select_prompt_segment(wav, SR) == (0, len(wav))


def test_picks_cleanest_window():
    # silence | tone 7s | silence 2s | tone 7s + loud noise bursts | silence | tone 7s
    w1 = _tone(7.0)
    noisy = _tone(7.0)
    # make second window "dirtier": interleave 200 ms dropouts every ~1 s so its
    # speech-frame ratio is lower than window 1's 100%
    seg = int(0.2 * SR)
    for start in range(0, len(noisy) - seg, int(1.0 * SR)):
        noisy[start : start + seg] = 0.0
    w3 = _tone(7.0, freq=330.0)
    wav = np.concatenate(
        [_silence(1.0), w1, _silence(2.0), noisy, _silence(2.0), w3, _silence(1.0)]
    )
    s0, s1 = select_prompt_segment(wav, SR, min_s=5.0, max_s=10.0)
    # expected: the first clean 7 s tone window (starts ~1 s in)
    assert abs(s0 - 1.0 * SR) < 0.3 * SR
    assert 6.0 * SR <= (s1 - s0) <= 8.0 * SR


def test_fallback_when_no_window_fits():
    # single 30 s continuous tone: no silence runs -> fallback first max_s trimmed
    wav = _tone(30.0)
    s0, s1 = select_prompt_segment(wav, SR, min_s=5.0, max_s=10.0)
    assert s0 == 0
    assert (s1 - s0) <= int(10.5 * SR)


def test_empty_input():
    assert select_prompt_segment(np.zeros(0, dtype=np.float32), SR) == (0, 0)
