#!/usr/bin/env python3
"""Pick the best 5-10 s clip per speaker from a prepare_tts_dataset manifest,
rank speakers, and emit <out-dir>/candidates.csv for human review.

Score = low CER + high SNR + high DNSMOS (when present). Gender balancing and a
text blocklist are applied before export. The human fills the blank
``region`` / ``name`` / ``approve`` columns after listening; approved rows
feed import_voices.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import string
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

CAND_MIN_DUR = 5.0
CAND_MAX_DUR = 10.0
DEFAULT_BLOCKLIST = Path(__file__).with_name("candidate_blocklist.txt")

CSV_COLUMNS = [
    "speaker",
    "gender",
    "gender_guess",
    "duration",
    "cer",
    "snr",
    "dnsmos",
    "median_f0_hz",
    "score",
    "wav",
    "text",
    "region_guess",
    "region_prob",
    "region",
    "name",
    "approve",
]

_PUNCT_RE = re.compile("[" + re.escape(string.punctuation) + "]")


def _norm_words(text: str) -> str:
    return " " + " ".join(_PUNCT_RE.sub(" ", text.lower()).split()) + " "


def clip_score(rec: dict) -> float:
    cer = rec.get("cer")
    cer_term = 1.0 - float(cer) if cer is not None else 0.5
    snr_term = min(float(rec.get("snr") or 0.0), 40.0) / 20.0
    dnsmos = rec.get("dnsmos")
    dnsmos_term = max(float(dnsmos) - 3.0, 0.0) if dnsmos is not None else 0.0
    return 2.0 * cer_term + snr_term + 0.5 * dnsmos_term


def load_blocklist(path: Path) -> list:
    if not path or not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = _norm_words(line).strip()
        if term:
            terms.append(term)
    return terms


def is_blocklisted(rec: dict, terms: list) -> bool:
    if not terms:
        return False
    padded = _norm_words(rec.get("text") or "")
    return any(f" {term} " in padded for term in terms)


def gender_from_f0(f0) -> str:
    if f0 is None:
        return ""
    if f0 >= 165.0:
        return "female"
    if f0 <= 150.0:
        return "male"
    return ""


def measure_f0(wav_path) -> float | None:
    from vizipvoice_addons.voice_meta import estimate_median_f0

    try:
        data, sr = sf.read(str(wav_path), dtype="float32")
        return estimate_median_f0(np.asarray(data), sr)
    except Exception as exc:
        print(f"[f0] {Path(wav_path).name}: {exc}", file=sys.stderr)
        return None


def effective_gender(rec: dict, f0=None) -> str:
    """Dataset gender when present, else inferred from measured F0."""
    from vizipvoice_addons.voice_meta import normalize_gender

    g = normalize_gender(rec.get("gender"))
    if g:
        return g
    return gender_from_f0(f0)


def balance_speakers(speaker_best: list, top: int) -> list:
    """Split the top-N speakers ~50/50 male/female; fill shortfalls from the
    other side, then from unknown-gender speakers."""
    half = top // 2
    male = [s for s in speaker_best if s[3] == "male"]
    female = [s for s in speaker_best if s[3] == "female"]
    unknown = [s for s in speaker_best if s[3] not in ("male", "female")]
    chosen = male[:half] + female[:half]
    need = top - len(chosen)
    for pool in (male[half:], female[half:], unknown):
        chosen += pool[: max(need, 0)]
        need = top - len(chosen)
        if need <= 0:
            break
    return chosen


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, help="manifest.jsonl from prepare_tts_dataset.py")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--top", type=int, default=10, help="top-N speakers to export")
    p.add_argument("--per-speaker", type=int, default=1, help="clips per speaker")
    p.add_argument("--min-dur", type=float, default=CAND_MIN_DUR)
    p.add_argument("--max-dur", type=float, default=CAND_MAX_DUR)
    p.add_argument(
        "--balance-gender",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="split top-N ~50/50 male/female (default: on)",
    )
    p.add_argument(
        "--blocklist",
        type=Path,
        default=DEFAULT_BLOCKLIST,
        help="one lowercase term per line; matching clips are skipped",
    )
    p.add_argument(
        "--exclude-dev-speakers",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="skip speakers listed in stats.json dev_speakers next to the manifest",
    )
    p.add_argument(
        "--guess-region",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="run the accent classifier; fills region_guess/region_prob",
    )
    p.add_argument(
        "--guess-region-clips",
        type=int,
        default=4,
        help="clips per speaker fed to the accent classifier (default: 4)",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    terms = load_blocklist(args.blocklist)

    excluded = set()
    if args.exclude_dev_speakers:
        stats_path = Path(args.manifest).with_name("stats.json")
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        excluded = set(stats.get("dev_speakers") or [])
        print(
            f"[exclude] {len(excluded)} dev speaker(s) from {stats_path}",
            file=sys.stderr,
        )

    records = []
    with open(args.manifest, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    by_speaker: dict = {}
    for rec in records:
        if not rec.get("kept"):
            continue
        dur = float(rec.get("duration") or 0.0)
        if not (args.min_dur <= dur <= args.max_dur):
            continue
        if not rec.get("wav") or not Path(rec["wav"]).is_file():
            continue
        key = rec.get("speaker") or "_unknown_"
        if key in excluded:
            continue
        rec["_score"] = clip_score(rec)
        by_speaker.setdefault(key, []).append(rec)

    for rows in by_speaker.values():
        rows.sort(key=lambda r: -r["_score"])

    # Per-speaker: drop blocklisted clips, keep the rest in score order.
    skipped = 0
    usable: dict = {}
    for spk, rows in by_speaker.items():
        kept_rows = []
        for rec in rows:
            if is_blocklisted(rec, terms):
                skipped += 1
            else:
                kept_rows.append(rec)
        if kept_rows:
            usable[spk] = kept_rows

    # Rank speakers by best clip; attach an effective gender per speaker so
    # --balance-gender can split male/female. F0 is only measured when the
    # dataset gives no gender (e.g. YodaLingua).
    ranked = []
    for spk, rows in usable.items():
        best = rows[0]
        gender = effective_gender(best)
        if not gender:
            gender = effective_gender(best, f0=measure_f0(best["wav"]))
        ranked.append((best["_score"], spk, rows, gender))
    ranked.sort(key=lambda t: (-t[0], t[1]))

    if args.balance_gender:
        chosen = balance_speakers(ranked, max(args.top, 0))
    else:
        chosen = ranked[: max(args.top, 0)]

    def _safe(spk: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in spk) or "spk"

    guess_region_fn = None
    if args.guess_region:
        from guess_region import guess_region as guess_region_fn

    rows_out = []
    for _score, speaker, rows, spk_gender in chosen:
        region_guess, region_prob = "", ""
        if guess_region_fn is not None:
            clips = [r["wav"] for r in rows[: max(args.guess_region_clips, 1)]]
            try:
                region_guess, region_prob = guess_region_fn(clips)
            except Exception as exc:
                print(f"[region] {speaker}: {exc}", file=sys.stderr)
        for k, rec in enumerate(rows[: max(args.per_speaker, 1)]):
            stem = _safe(speaker) if k == 0 else f"{_safe(speaker)}_{k}"
            dest_wav = out_dir / f"{stem}.wav"
            shutil.copyfile(rec["wav"], dest_wav)
            txt_path = dest_wav.with_suffix(".txt")
            txt_path.write_text(rec.get("text", ""), encoding="utf-8")
            median_f0 = measure_f0(dest_wav)
            gender = rec.get("gender") or ""
            rows_out.append(
                {
                    "speaker": speaker,
                    "gender": gender,
                    "gender_guess": spk_gender or effective_gender(rec, f0=median_f0),
                    "duration": rec.get("duration"),
                    "cer": rec.get("cer"),
                    "snr": rec.get("snr"),
                    "dnsmos": rec.get("dnsmos"),
                    "median_f0_hz": round(median_f0, 1) if median_f0 else "",
                    "score": round(rec["_score"], 4),
                    "wav": str(dest_wav.resolve()),
                    "text": rec.get("text", ""),
                    "region_guess": region_guess,
                    "region_prob": round(region_prob, 4) if region_prob else "",
                    "region": "",
                    "name": "",
                    "approve": "",
                }
            )

    csv_path = out_dir / "candidates.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows_out)

    print(
        f"[done] {len(rows_out)} candidate clip(s) from {len(chosen)} speaker(s) "
        f"({skipped} blocklist-skipped) -> {csv_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
