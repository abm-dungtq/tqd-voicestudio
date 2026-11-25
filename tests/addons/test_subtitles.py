from vizipvoice_addons.subtitles import (
    _split_text_to_cues,
    segment_to_cues,
    segments_to_srt,
    segments_to_vtt,
)


def test_split_text_to_cues_max_words():
    text = " ".join(f"w{i}" for i in range(30))
    cues = _split_text_to_cues(text)
    assert all(len(c.split()) <= 12 for c in cues)
    assert " ".join(cues) == text
    assert _split_text_to_cues("") == []


def test_split_prefers_punctuation_break():
    text = "một hai ba bốn năm. sáu bảy tám chín mười."
    cues = _split_text_to_cues(text)
    assert cues[0].endswith("năm.")


def test_segment_to_cues_time_proportional():
    seg = {"start": 1.0, "end": 4.0, "text": "aa bb cc ddee", "voice": "v"}
    cues = segment_to_cues(seg)
    # <=12 words so a single cue covering the whole span
    assert len(cues) == 1
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 4.0

    long_text = " ".join(f"w{i}" for i in range(36))  # -> 3 cues of 12 words
    seg2 = {"start": 0.0, "end": 9.0, "text": long_text}
    cues2 = segment_to_cues(seg2)
    assert len(cues2) == 3
    assert cues2[0]["start"] == 0.0
    assert cues2[-1]["end"] == 9.0
    for a, b in zip(cues2, cues2[1:]):
        assert b["start"] == a["end"]


def test_srt_and_vtt_format():
    segs = [
        {"start": 0.0, "end": 1.5, "text": "xin chào", "voice": "v1"},
        {"start": 1.75, "end": 3.0, "text": "thế giới", "voice": "v1"},
    ]
    srt = segments_to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\nxin chào" in srt
    assert "2\n00:00:01,750 --> 00:00:03,000\nthế giới" in srt
    vtt = segments_to_vtt(segs)
    assert vtt.startswith("WEBVTT")
    assert "00:00:01.750 --> 00:00:03.000" in vtt
    assert segments_to_srt([]) == ""
