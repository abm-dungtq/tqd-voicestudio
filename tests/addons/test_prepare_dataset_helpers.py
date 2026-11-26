import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "data"))
import prepare_tts_dataset as ptd  # noqa: E402


def _resolve_tokens() -> str:
    """Locate the real tokenizer vocab portably.

    Order: explicit env override, the dev-box path, the installed model dir,
    then a small one-off download from the HF model repo (CI machines).
    """
    env = os.environ.get("TQD_TOKENS_TXT")
    candidates = [env] if env else []
    candidates.append("D:/TTS/models/ViZipvoice/tokens.txt")
    try:
        from tqd_voicestudio import paths

        candidates.append(str(paths.model_dir() / "tokens.txt"))
    except ImportError:
        pass
    for cand in candidates:
        if cand and Path(cand).is_file():
            return cand
    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        "contextboxai/ViZipvoice",
        "tokens.txt",
        revision="7903dcb99d5a17c2e59a0ef5f7db32de4bed357b",
    )


TOKENS = _resolve_tokens()


def test_load_token_vocab():
    vocab = ptd.load_token_vocab(TOKENS)
    assert "a" in vocab and "đ" in vocab
    assert len(vocab) > 50


def test_oov_chars():
    vocab = set("abcđ ")
    assert ptd.oov_chars("abc đ", vocab) == set()
    assert ptd.oov_chars("abcxyz", vocab) == {"x", "y", "z"}
    # spaces pass even when the vocab has no space token
    assert ptd.oov_chars("a b", set("ab")) == set()


def test_oov_against_real_vocab():
    vocab = ptd.load_token_vocab(TOKENS)
    # common Vietnamese words should be fully covered
    assert ptd.oov_chars("xin chào việt nam", vocab) == set()
    # an emoji / CJK char is out of vocab
    assert ptd.oov_chars("hello 世界", vocab) != set()


def test_estimate_snr_db():
    sr = 24000
    # loud tone + quiet segments -> high dynamic range
    loud = 0.5 * np.sin(2 * np.pi * 200 * np.arange(sr * 4) / sr)
    quiet = 0.001 * np.sin(2 * np.pi * 200 * np.arange(sr * 4) / sr)
    wav = np.concatenate([quiet, loud]).astype(np.float32)
    snr = ptd.estimate_snr_db(wav, sr)
    assert snr > 30.0
    # constant-level signal -> low SNR estimate
    flat = np.full(sr * 2, 0.1, dtype=np.float32)
    assert ptd.estimate_snr_db(flat, sr) < 1.0
    assert ptd.estimate_snr_db(np.zeros(10, dtype=np.float32), sr) == 0.0


def test_decode_audio_bytes(tmp_path):
    import io

    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.zeros(2400, dtype=np.float32), 24000, format="WAV")
    sample = {"audio": {"bytes": buf.getvalue(), "path": None}}
    wav, sr = ptd._decode_audio(sample, "audio")
    assert sr == 24000 and len(wav) == 2400


def test_derive_sample_id():
    assert ptd.derive_sample_id({"_id": "abc 123.wav"}, "org/DS", 7) == "ds-abc_123.wav"
    assert ptd.derive_sample_id({"__key__": "k9"}, "org/DS", 7) == "ds-k9"
    assert ptd.derive_sample_id({"id": "x1"}, "org/DS", 7) == "ds-x1"
    assert ptd.derive_sample_id({}, "org/DS", 7) == "ds-000007"
    # _id preferred over __key__/id
    assert ptd.derive_sample_id(
        {"_id": "a", "__key__": "b", "id": "c"}, "org/DS", 1
    ) == "ds-a"


def _mk_kept(speaker, n, dur=6.0, start=0):
    return [
        {
            "id": f"x-{i:05d}",
            "speaker": speaker,
            "duration": dur,
            "text": "t",
            "wav": f"/w/{i}.wav",
            "kept": True,
        }
        for i in range(start, start + n)
    ]


def test_split_train_dev_skewed():
    import resplit

    # One dominant speaker holds 60% of 1000 rows; 100 others hold 4 each.
    recs = _mk_kept("BIG", 600) + sum(
        (_mk_kept(f"s{k:03d}", 4, start=600 + k * 4) for k in range(100)), []
    )
    train, dev, dev_spk = resplit.split_train_dev(
        recs, dev_frac=0.01, min_dev_speakers=3, max_dev_speakers=20, seed=0
    )
    assert len(dev) <= 20  # <= ~2% of 1000 rows
    assert "BIG" not in dev_spk
    assert len(dev_spk) >= 3
    assert len(train) + len(dev) == len(recs)
    # deterministic
    t2, d2, s2 = resplit.split_train_dev(
        recs, dev_frac=0.01, min_dev_speakers=3, max_dev_speakers=20, seed=0
    )
    assert dev_spk == s2 and len(d2) == len(dev)


def test_split_train_dev_no_speakers():
    import resplit

    recs = _mk_kept("", 200)
    train, dev, dev_spk = resplit.split_train_dev(recs, dev_frac=0.01, seed=0)
    assert len(dev) == 2 and len(train) == 198


class _FakeDS:
    """Iterable fake dataset with a datasets-style .skip(); can die at row N."""

    def __init__(self, samples, die_at=None):
        self.samples = samples
        self.die_at = die_at

    def __iter__(self):
        for i, s in enumerate(self.samples):
            if self.die_at is not None and i >= self.die_at:
                raise RuntimeError("simulated crash")
            yield s

    def skip(self, n):
        return _FakeDS(self.samples[n:], self.die_at)


def _fake_sample(i, text="xin chào bạn", dur=1.2):
    import io

    import soundfile as sf

    sr = 24000
    t = np.arange(int(sr * dur), dtype=np.float32) / sr
    tone = 0.2 * np.sin(2 * np.pi * 220 * t)
    tone[: int(sr * dur * 0.3)] = 0  # leading silence -> high SNR estimate
    buf = io.BytesIO()
    sf.write(buf, tone, sr, format="WAV", subtype="PCM_16")
    return {
        "_id": f"utt{i:03d}",
        "audio": {"bytes": buf.getvalue()},
        "text": text,
        "speaker": f"spk{i % 5}",
    }


def _fake_args(out_dir, **kw):
    import argparse

    base = dict(
        dataset="fake/ds",
        config=None,
        split="train",
        data_files=None,
        text_col="text",
        audio_col="audio",
        speaker_col="speaker",
        gender_col=None,
        dnsmos_col=None,
        streaming=True,
        max_samples=None,
        resume=False,
        out_dir=str(out_dir),
        tokens=TOKENS,
        min_dur=1.0,
        max_dur=20.0,
        min_snr=15.0,
        min_dnsmos=3.2,
        asr_filter=False,
        max_cer=0.10,
        asr_batch_size=4,
        dev_speakers=3,
        dev_frac=0.01,
        max_dev_speakers=20,
        license="",
        seed=0,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _manifest_ids(out_dir):
    import json

    ids = []
    for line in open(out_dir / "manifest.jsonl", encoding="utf-8"):
        ids.append(json.loads(line)["id"])
    return ids


def test_resume_after_crash(tmp_path, monkeypatch):
    import pytest

    out = tmp_path / "ds"
    samples = [_fake_sample(i) for i in range(20)]
    calls = {"n": 0}

    def fake_load(args):
        # first call dies after 7 samples, later calls serve all
        calls["n"] += 1
        return _FakeDS(samples, die_at=7 if calls["n"] == 1 else None)

    monkeypatch.setattr(ptd, "load_ds", fake_load)
    with pytest.raises(RuntimeError):
        ptd.main(_argv(_fake_args(out)))
    first_ids = _manifest_ids(out)
    assert len(first_ids) == 7

    # resume: contiguous prefix -> exercises ds.skip()
    ptd.main(_argv(_fake_args(out, resume=True)))
    ids = _manifest_ids(out)
    assert len(ids) == len(set(ids)) == 20
    import json

    kept = sum(
        json.loads(l).get("kept", False)
        for l in open(out / "manifest.jsonl", encoding="utf-8")
    )
    assert kept == len(list((out / "wavs").glob("*.wav")))


def _argv(ns):
    out = []
    for k, v in vars(ns).items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                out.append(flag)
        elif v is not None:
            out += [flag, str(v)]
    return out


def test_resume_after_crash_with_asr(tmp_path, monkeypatch):
    import pytest

    import vizipvoice_addons.asr as asr_mod

    out = tmp_path / "ds"
    # Every 3rd sample is too short -> early reject, written immediately, so
    # the crashed manifest has a non-contiguous set of ids.
    samples = [
        _fake_sample(i, dur=0.5 if i % 3 == 0 else 1.2) for i in range(30)
    ]
    calls = {"n": 0}

    def fake_load(args):
        calls["n"] += 1
        return _FakeDS(samples, die_at=12 if calls["n"] == 1 else None)

    monkeypatch.setattr(ptd, "load_ds", fake_load)
    monkeypatch.setattr(
        asr_mod, "transcribe", lambda wavs, sr, batch_size=8: ["xin chào bạn"] * len(wavs)
    )
    argv = _argv(_fake_args(out, asr_filter=True))
    with pytest.raises(RuntimeError):
        ptd.main(argv)
    first = _manifest_ids(out)
    # 12 processed; some written, some still pending in the ASR batch.
    assert 0 < len(first) < 30

    ptd.main(_argv(_fake_args(out, asr_filter=True, resume=True)))
    ids = _manifest_ids(out)
    assert len(ids) == len(set(ids)) == 30
    import json
    kept = sum(json.loads(l).get("kept", False) for l in open(out / "manifest.jsonl", encoding="utf-8"))
    assert kept == len(list((out / "wavs").glob("*.wav")))
