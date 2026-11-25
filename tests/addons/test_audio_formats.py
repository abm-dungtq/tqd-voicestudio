import shutil
import subprocess

import numpy as np
import pytest

from vizipvoice_addons.audio_formats import encode_pcm16

SR = 24000
t = np.arange(SR // 2, dtype=np.float32) / SR
PCM = (np.clip(np.sin(2 * np.pi * 440 * t), -1, 1) * 32767).astype("<i2").tobytes()
FFMPEG = shutil.which("ffmpeg")


def test_pcm_passthrough():
    data, mt = encode_pcm16(PCM, SR, "pcm")
    assert data == PCM
    assert "audio/pcm" in mt


def test_wav_header():
    data, mt = encode_pcm16(PCM, SR, "wav")
    assert mt == "audio/wav"
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    assert len(data) == len(PCM) + 44


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_mp3():
    data, mt = encode_pcm16(PCM, SR, "mp3")
    assert mt == "audio/mpeg"
    assert data[:3] == b"ID3" or data[:2] == b"\xff\xfb"
    assert len(data) > 1000


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_opus():
    data, mt = encode_pcm16(PCM, SR, "opus")
    assert mt == "audio/ogg"
    assert data[:4] == b"OggS"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
def test_flac():
    data, mt = encode_pcm16(PCM, SR, "flac")
    assert mt == "audio/flac"
    assert data[:4] == b"fLaC"


def test_bad_format():
    with pytest.raises(ValueError):
        encode_pcm16(PCM, SR, "aac")
