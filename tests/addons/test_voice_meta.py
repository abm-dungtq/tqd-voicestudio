import numpy as np
import pytest

from vizipvoice_addons.voice_meta import (
    filter_voices,
    normalize_gender,
    normalize_region,
    parse_voice_name,
    read_sidecar,
    write_sidecar,
)


@pytest.mark.parametrize(
    "name,region,gender,style",
    [
        ("vi-bac-nam-ducanh-tunhien-v1", "Bắc", "male", "tunhien"),
        ("vi-nam-nu-haiyen-tunhien-v1", "Nam", "female", "tunhien"),
        ("vi-trung-nu-thanhvan-tintuc-v1", "Trung", "female", "tintuc"),
        ("vi-bac-nam-a1-b2-v12", "Bắc", "male", "b2"),
    ],
)
def test_parse_voice_name_ok(name, region, gender, style):
    parsed = parse_voice_name(name)
    assert parsed is not None
    assert parsed["region"] == region
    assert parsed["gender"] == gender
    assert parsed["style"] == style


@pytest.mark.parametrize(
    "name",
    [
        "Đinh-Quyết",
        "vi-bac-nam-ducanh-v1",  # missing style segment
        "vi-bac-nam-duc-anh-tunhien-v1",  # hyphen inside name segment
        "vi-bac-nam-ducanh-tunhien",
        "en-bac-nam-ducanh-tunhien-v1",
        "",
    ],
)
def test_parse_voice_name_rejects(name):
    assert parse_voice_name(name) is None


@pytest.mark.parametrize(
    "inp,expected",
    [
        ("bac", "Bắc"),
        ("Bắc", "Bắc"),
        ("BẮC", "Bắc"),
        ("north", "Bắc"),
        ("trung", "Trung"),
        ("Trung", "Trung"),
        ("central", "Trung"),
        ("nam", "Nam"),
        ("Nam", "Nam"),
        ("south", "Nam"),
        ("miền nam", None),
        ("", None),
        (None, None),
        ("xyz", None),
    ],
)
def test_normalize_region(inp, expected):
    assert normalize_region(inp) == expected


@pytest.mark.parametrize(
    "inp,expected",
    [
        ("male", "male"),
        ("Male", "male"),
        ("nam", "male"),
        ("NAM", "male"),
        ("female", "female"),
        ("nu", "female"),
        ("nữ", "female"),
        ("Nữ", "female"),
        ("", None),
        (None, None),
        ("other", None),
    ],
)
def test_normalize_gender(inp, expected):
    assert normalize_gender(inp) == expected


def test_sidecar_roundtrip(tmp_path):
    p = tmp_path / "v.json"
    assert read_sidecar(p) == {}
    write_sidecar(p, {"gender": "female", "region": "Nam", "x": 1})
    assert read_sidecar(p) == {"gender": "female", "region": "Nam", "x": 1}
    p.write_text("{not json", encoding="utf-8")
    assert read_sidecar(p) == {}


VOICES = [
    {"id": "vi-bac-nam-a-tunhien-v1", "description": "Bắc male", "gender": "male",
     "region": "Bắc", "kind": "custom", "aliases": ["duc anh"]},
    {"id": "vi-nam-nu-b-tunhien-v1", "description": "Nam female", "gender": "female",
     "region": "Nam", "kind": "custom", "aliases": []},
    {"id": "Đinh-Quyết", "description": "Studio voice Bắc", "gender": "male",
     "region": "Bắc", "kind": "preset", "aliases": ["Minh Đức"]},
]


def test_filter_voices_gender_region_kind():
    assert len(filter_voices(VOICES, gender="male")) == 2
    # gender="nam" -> male, region="nam" -> Nam: no male voice in Nam
    assert filter_voices(VOICES, gender="nam", region="nam") == []
    assert filter_voices(VOICES, gender="male", region="Nam") == []
    assert [v["id"] for v in filter_voices(VOICES, gender="female", region="south")] == [
        "vi-nam-nu-b-tunhien-v1"
    ]
    assert len(filter_voices(VOICES, kind="preset")) == 1
    assert len(filter_voices(VOICES, kind="custom", region="Bắc")) == 1


def test_filter_voices_q():
    assert [v["id"] for v in filter_voices(VOICES, q="minh")] == ["Đinh-Quyết"]
    assert [v["id"] for v in filter_voices(VOICES, q="STUDIO")] == ["Đinh-Quyết"]
    assert len(filter_voices(VOICES, q="vi-")) == 2
    assert filter_voices(VOICES, q="zzz") == []


def _harmonic_tone(freq, sr, dur=0.5, gap_at=None):
    """Harmonic tone (5 harmonics) with an optional silence gap."""
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float32) / sr
    wav = np.zeros(n, dtype=np.float32)
    for h in range(1, 6):
        wav += (0.3 / h) * np.sin(2 * np.pi * freq * h * t)
    if gap_at is not None:
        g0, g1 = gap_at
        wav[int(g0 * sr) : int(g1 * sr)] = 0.0
    return wav


@pytest.mark.parametrize("freq", [110.0, 220.0])
def test_estimate_median_f0(freq):
    from vizipvoice_addons.voice_meta import estimate_median_f0

    sr = 24000
    wav = np.concatenate(
        [
            _harmonic_tone(freq, sr, 0.4),
            np.zeros(int(0.2 * sr), dtype=np.float32),
            _harmonic_tone(freq, sr, 0.4, gap_at=(0.2, 0.3)),
        ]
    )
    f0 = estimate_median_f0(wav, sr)
    assert f0 is not None
    assert abs(f0 - freq) / freq < 0.05
    assert estimate_median_f0(np.zeros(sr, dtype=np.float32), sr) is None
