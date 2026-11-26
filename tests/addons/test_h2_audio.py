import numpy as np
import pytest

from vizipvoice_addons import mixing, pauses, script_roles

SR = 24000


def tone(seconds, freq=220.0, amp=0.1):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_pause_parser_parts_and_values():
    parts = pauses.split_pauses("Xin chào. [pause:1.5] Tạm biệt [pause:2s] hết")
    assert [p[0] for p in parts] == ["text", "pause", "text", "pause", "text"]
    assert parts[1][1] == 1.5 and parts[3][1] == 2.0
    assert pauses.strip_pauses("a [pause:3] b") == "a b"


@pytest.mark.parametrize("bad", ["[pause:0]", "[pause:10.5]", "[pause:11]"])
def test_pause_out_of_range(bad):
    with pytest.raises(pauses.PauseError):
        pauses.split_pauses("x " + bad + " y")


def test_pause_with_voice_tags_kept_in_text_parts():
    parts = pauses.split_pauses("[voice=A] một [pause:1] [voice=B] hai")
    assert parts[0] == ("text", "[voice=A] một ") and parts[1] == ("pause", 1.0)


def test_roles_parse_and_apply():
    text = "Lan: Chào anh.\nNam: Chào chị.\nTiếp tục lời Nam.\n\nLan: Tạm biệt."
    assert script_roles.parse_roles(text) == ["Lan", "Nam"]
    out = script_roles.apply_cast(text, {"Lan": "V1", "Nam": "V2"})
    lines = out.splitlines()
    assert lines[0] == "[voice=V1] Chào anh."
    assert lines[2] == "[voice=V2] Tiếp tục lời Nam."
    assert lines[-1] == "[voice=V1] Tạm biệt."


def test_roles_unknown_and_not_roles():
    with pytest.raises(KeyError) as exc:
        script_roles.apply_cast("A: x\nB: y", {"A": "V"})
    assert exc.value.args[0] == ["B"]
    assert script_roles.parse_roles("Giá: 5 triệu, rất rẻ.\nMột câu thường.") == ["Giá"]
    assert script_roles.parse_roles("Xin chào, ông ấy nói: rất vui.") == []
    assert script_roles.parse_roles("Một hai ba bốn năm: không phải vai") == []


def test_normalize_hits_target_lufs():
    import pyloudnorm

    x = tone(3.0, amp=0.02) + tone(3.0, freq=440, amp=0.01)
    y = mixing.normalize_loudness(x, SR, -16.0)
    lufs = pyloudnorm.Meter(SR).integrated_loudness(y.astype(np.float64))
    assert abs(lufs - (-16.0)) < 0.5
    assert np.max(np.abs(y)) <= 10 ** (-1 / 20) + 1e-4


def test_trim_shortens_long_silence_but_keeps_protected():
    sp = tone(1.0)
    sil = np.zeros(int(2.0 * SR), dtype=np.float32)
    wav = np.concatenate([sp, sil, sp, sil, sp])
    protected = [(len(sp) + len(sil) + len(sp), len(sp) + len(sil) + len(sp) + len(sil))]
    out, cuts = mixing.trim_silences(wav, SR, protected=protected, max_silence_s=0.6)
    assert len(cuts) == 1
    assert abs((len(wav) - len(out)) - (len(sil) - int(0.6 * SR))) < SR * 0.05
    assert mixing.remap_time(3.5, cuts, SR) < 3.5 - 1.0


def test_trim_leaves_short_silence():
    wav = np.concatenate([tone(1.0), np.zeros(int(0.5 * SR), dtype=np.float32), tone(1.0)])
    out, cuts = mixing.trim_silences(wav, SR)
    assert cuts == [] and len(out) == len(wav)


def test_music_loops_fades_and_ducks():
    speech = np.concatenate([tone(2.0, amp=0.1), np.zeros(int(2.0 * SR), dtype=np.float32), tone(2.0, amp=0.1)])
    music = tone(1.0, freq=330, amp=0.1)
    out = mixing.mix_music(speech, music, SR, volume_db=-12, duck_db=-12, fade_s=1.0)
    assert len(out) == len(speech)

    def rms_db(seg):
        return 20 * np.log10(np.sqrt(np.mean(seg.astype(np.float64) ** 2)) + 1e-9)

    under_speech = rms_db((out - speech)[int(0.5 * SR) : int(1.5 * SR)])
    in_gap = rms_db(out[int(3.0 * SR) : int(3.8 * SR)])
    assert in_gap - under_speech > 9.0
    assert rms_db(out[-int(0.1 * SR) :] - speech[-int(0.1 * SR) :]) < in_gap - 12


def test_normalize_peaky_material_still_hits_target_under_ceiling():
    import pyloudnorm

    rng = np.random.default_rng(0)
    x = tone(6.0, amp=0.02) + tone(6.0, freq=523, amp=0.015)
    x[::4000] += 0.8
    x = np.clip(x, -1, 1).astype(np.float32)
    y = mixing.normalize_loudness(x, SR, -16.0)
    lufs = pyloudnorm.Meter(SR).integrated_loudness(y.astype(np.float64))
    assert abs(lufs + 16.0) < 0.5, lufs
    assert np.max(np.abs(y)) <= 10 ** (-1 / 20) + 1e-6
