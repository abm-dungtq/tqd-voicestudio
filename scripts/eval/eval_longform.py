#!/usr/bin/env python3
"""Long-form A/B evaluation of chunkers (legacy vs v2) through the timed endpoint.

  python scripts/eval/eval_longform.py run --server http://127.0.0.1:8011 \
      --texts plans/261125-1606-tqd-voice/assets/longform_texts.json \
      --out-dir D:/TTS/outputs/chunker_eval/legacy --label legacy --repeats 2
  python scripts/eval/eval_longform.py compare base/summary.json cand/summary.json

Per segment (start/end/text from /v1/audio/speech/timed) the audio slice is
transcribed with the ASR and scored with the same normalisation as eval_tts.py.
Decision (compare): ADOPT_V2 if cand.char_cer <= base.char_cer - 0.005 AND
cand.bad_char_share <= base.bad_char_share, else KEEP_LEGACY.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SR = 24000
BAD_CER = 0.3
ASR_BATCH = 8


def _http_json(url: str, body: dict | None = None, timeout: float = 600):
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run(args) -> int:
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio

    from eval_tts import _canon
    from vizipvoice_addons import asr
    from vizipvoice_addons.chunker import weight
    from zipvoice.vizipvoice import normalize_vietnamese_text

    server = args.server.rstrip("/")
    health = _http_json(f"{server}/health")
    chunker = health.get("chunker")
    if chunker != args.label:
        print(f"server chunker={chunker!r} does not match --label {args.label!r}", file=sys.stderr)
        return 2

    data = json.loads(Path(args.texts).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    (out_dir / "wavs").mkdir(parents=True, exist_ok=True)
    norm = lambda t: normalize_vietnamese_text(t, enabled=True)  # noqa: E731

    t0 = time.time()
    rows = []
    n_docs = 0
    total_audio_s = 0.0
    for repeat in range(args.repeats):
        for item in data["texts"]:
            for voice in data["voices"]:
                resp = _http_json(
                    f"{server}/v1/audio/speech/timed",
                    {
                        "input": item["text"],
                        "voice": voice,
                        "response_format": "wav",
                        "sample_rate": SR,
                        "quality_check": False,
                        "subtitle_format": "none",
                    },
                    timeout=600,
                )
                wav, sr = sf.read(io.BytesIO(base64.b64decode(resp["audio_base64"])), dtype="float32", always_2d=True)
                wav = wav.mean(axis=1).astype(np.float32)
                assert sr == SR, sr
                sf.write(str(out_dir / "wavs" / f"{item['id']}_{voice}_{repeat}.wav"), wav, SR, subtype="PCM_16")
                total_audio_s += len(wav) / SR
                n_docs += 1

                clips, metas = [], []
                for idx, seg in enumerate(resp["segments"]):
                    a = max(0, int((seg["start"] - 0.05) * SR))
                    b = min(len(wav), int((seg["end"] + 0.05) * SR))
                    if b <= a:
                        continue
                    t = torch.from_numpy(wav[a:b]).unsqueeze(0)
                    clips.append(torchaudio.functional.resample(t, SR, 16000).squeeze(0).numpy())
                    metas.append((idx, seg["text"]))
                hyps = []
                for i in range(0, len(clips), ASR_BATCH):
                    hyps.extend(asr.transcribe(clips[i:i + ASR_BATCH], 16000))
                for (idx, text), hyp in zip(metas, hyps):
                    ref_norm = _canon(asr.normalize_for_compare(text, normalizer=norm))
                    hyp_norm = _canon(asr.normalize_for_compare(hyp, normalizer=norm))
                    chars = len(ref_norm)
                    if chars == 0:
                        continue
                    cer = asr.cer(ref_norm, hyp_norm)
                    rows.append({
                        "text_id": item["id"], "voice": voice, "repeat": repeat,
                        "seg_idx": idx, "words": weight(text), "chars": chars,
                        "cer": round(cer, 4), "asr": hyp,
                    })
                print(f"[longform] {args.label} r{repeat} {item['id']} {voice}: "
                      f"{len(resp['segments'])} segs", flush=True)

    with open(out_dir / "segments.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    sum_chars = sum(r["chars"] for r in rows)
    sum_edits = sum(r["cer"] * r["chars"] for r in rows)
    bad_chars = sum(r["chars"] for r in rows if r["cer"] > BAD_CER)
    summary = {
        "label": args.label,
        "chunker": chunker,
        "n_docs": n_docs,
        "n_segments": len(rows),
        "mean_segment_words": round(sum(r["words"] for r in rows) / len(rows), 3) if rows else 0.0,
        "char_cer": round(sum_edits / sum_chars, 5) if sum_chars else 0.0,
        "bad_char_share": round(bad_chars / sum_chars, 5) if sum_chars else 0.0,
        "total_audio_s": round(total_audio_s, 2),
        "wall_s": round(time.time() - t0, 1),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def compare(args) -> int:
    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    lines = []
    for key in ("n_docs", "n_segments", "mean_segment_words", "char_cer",
                "bad_char_share", "total_audio_s", "wall_s"):
        lines.append(f"{key}: {base[key]} -> {cand[key]}")
    adopt = (cand["char_cer"] <= base["char_cer"] - 0.005
             and cand["bad_char_share"] <= base["bad_char_share"])
    lines.append("ADOPT_V2" if adopt else "KEEP_LEGACY")
    text = "\n".join(lines)
    print(text)
    (Path(args.candidate).parent / "compare.txt").write_text(text + "\n", encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--server", required=True)
    r.add_argument("--texts", required=True)
    r.add_argument("--out-dir", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--repeats", type=int, default=2)
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("candidate")
    args = ap.parse_args()
    return run(args) if args.cmd == "run" else compare(args)


if __name__ == "__main__":
    sys.exit(main())
