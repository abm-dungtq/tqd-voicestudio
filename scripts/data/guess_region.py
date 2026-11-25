"""Guess a speaker's region (Bắc/Trung/Nam) with a wav2vec2 accent classifier.

Model: thangquang09/wav2vec2-base-vi-accent-classification (labels
north/central/south). Each clip is decoded with soundfile, downmixed to mono,
resampled to 16 kHz and truncated to 30 s; class probabilities are averaged
across the given clips.

CLI:
    python scripts/data/guess_region.py guess a.wav [b.wav ...]
    python scripts/data/guess_region.py validate \
        --voices-dir D:/TTS/voices \
        --audio-dir D:/TTS/models/ViZipvoice/audio \
        --studio-meta studio_voices_meta.json \
        --out D:/TTS/outputs/region_validation.json
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vizipvoice_addons.voice_meta import normalize_region  # noqa: E402

MODEL_ID = "thangquang09/wav2vec2-base-vi-accent-classification"
TARGET_SR = 16000
MAX_SECONDS = 30.0
REGIONS = ("Bắc", "Trung", "Nam")

_state: dict = {}


def _load_model():
    if not _state:
        import torch
        from transformers import (
            AutoFeatureExtractor,
            AutoModelForAudioClassification,
        )

        fe = AutoFeatureExtractor.from_pretrained(MODEL_ID)
        model = AutoModelForAudioClassification.from_pretrained(MODEL_ID)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device).eval()
        id2label = {
            int(k): v for k, v in model.config.id2label.items()
        }
        _state.update(
            fe=fe, model=model, device=device, id2label=id2label, torch=torch
        )
    return _state


def _load_clip(path, sr: int = TARGET_SR, max_s: float = MAX_SECONDS):
    import torchaudio

    wav, in_sr = sf.read(str(path), dtype="float32", always_2d=False)
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    if in_sr != sr:
        import torch

        t = torch.from_numpy(wav)
        t = torchaudio.functional.resample(t, in_sr, sr)
        wav = t.numpy()
    return wav[: int(max_s * sr)]


def clip_probs(path) -> dict:
    """Return {raw_label: prob} for one clip."""
    st = _load_model()
    torch = st["torch"]
    wav = _load_clip(path)
    inputs = st["fe"](wav, sampling_rate=TARGET_SR, return_tensors="pt")
    inputs = {k: v.to(st["device"]) for k, v in inputs.items()}
    with torch.no_grad():
        logits = st["model"](**inputs).logits[0]
    probs = torch.softmax(logits, dim=-1).cpu().numpy()
    return {st["id2label"][i]: float(probs[i]) for i in range(len(probs))}


def region_probs(raw_probs: dict) -> dict:
    """Aggregate raw model labels into canonical regions."""
    agg = {r: 0.0 for r in REGIONS}
    other = 0.0
    for label, p in raw_probs.items():
        region = normalize_region(label)
        if region in agg:
            agg[region] += float(p)
        else:
            other += float(p)
    if other:
        agg["_other"] = other
    return agg


def guess_region(wav_paths) -> tuple:
    """Average class probs over clips -> (region in {Bắc,Trung,Nam}, prob)."""
    paths = list(wav_paths)
    if not paths:
        raise ValueError("guess_region needs at least one wav path")
    total = None
    for p in paths:
        rp = region_probs(clip_probs(p))
        if total is None:
            total = {r: rp.get(r, 0.0) for r in REGIONS}
        else:
            for r in REGIONS:
                total[r] += rp.get(r, 0.0)
    avg = {r: total[r] / len(paths) for r in REGIONS}
    region = max(avg, key=avg.get)
    return region, avg[region]


def _norm_stem(stem: str) -> str:
    return unicodedata.normalize("NFC", stem)


def _iter_voice_items(voices_dir: Path):
    for wav in sorted(voices_dir.glob("*.wav")):
        name = wav.stem
        if name.startswith("__style"):
            continue
        parts = name.split("-")
        if len(parts) < 2 or parts[0] != "vi":
            continue
        label = normalize_region(parts[1])
        if label is None:
            continue
        yield {"name": name, "source": "voices", "label": label, "wav": wav}


def _iter_studio_items(audio_dir: Path, meta_path: Path):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    voices = meta.get("voices", {})
    for name, info in voices.items():
        label = normalize_region(info.get("region", ""))
        if label is None:
            continue
        wav = audio_dir / f"{name}.wav"
        if not wav.exists():
            continue
        yield {"name": name, "source": "studio", "label": label, "wav": wav}


def cmd_validate(args) -> int:
    items = list(_iter_voice_items(Path(args.voices_dir)))
    items += list(_iter_studio_items(Path(args.audio_dir), Path(args.studio_meta)))
    results = []
    confusion = {lab: {pred: 0 for pred in REGIONS} for lab in REGIONS}
    correct = 0
    for it in items:
        pred, prob = guess_region([it["wav"]])
        ok = pred == it["label"]
        correct += int(ok)
        confusion[it["label"]][pred] += 1
        results.append(
            {
                "name": it["name"],
                "source": it["source"],
                "label": it["label"],
                "prediction": pred,
                "prob": round(prob, 4),
                "correct": ok,
            }
        )
        print(
            f"{'OK ' if ok else 'BAD'} {it['name']}: {it['label']} -> {pred} "
            f"({prob:.3f})",
            flush=True,
        )
    accuracy = correct / len(results) if results else 0.0
    out = {
        "model": MODEL_ID,
        "n": len(results),
        "correct": correct,
        "accuracy": accuracy,
        "confusion": confusion,
        "items": results,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"accuracy {correct}/{len(results)} = {accuracy:.3f} -> {args.out}")
    return 0


def cmd_guess(args) -> int:
    region, prob = guess_region(args.wavs)
    print(json.dumps({"region": region, "prob": prob}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("guess")
    g.add_argument("wavs", nargs="+")
    g.set_defaults(fn=cmd_guess)
    v = sub.add_parser("validate")
    v.add_argument("--voices-dir", default="D:/TTS/voices")
    v.add_argument("--audio-dir", default="D:/TTS/models/ViZipvoice/audio")
    v.add_argument("--studio-meta", default="studio_voices_meta.json")
    v.add_argument("--out", required=True)
    v.set_defaults(fn=cmd_validate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
