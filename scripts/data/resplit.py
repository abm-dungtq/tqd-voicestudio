#!/usr/bin/env python3
"""Re-split a finished prepare_tts_dataset manifest into custom_train.tsv /
custom_dev.tsv and update stats.json.

Dev speakers are drawn at random from those whose clip count lies between the
20th and 80th percentile of per-speaker counts (robust to skewed speaker
distributions), adding speakers until dev hours reach --dev-frac of total kept
hours, bounded by --min-dev-speakers / --max-dev-speakers. Without speaker ids
it falls back to a random dev-frac row split.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np


def split_train_dev(
    kept_records: list,
    dev_frac: float = 0.01,
    min_dev_speakers: int = 3,
    max_dev_speakers: int = 20,
    seed: int = 0,
    has_speaker_col: bool = True,
):
    """Return ``(train, dev, dev_speakers)`` from kept manifest records."""
    rng = random.Random(seed)
    speakers = {r["speaker"] for r in kept_records if r.get("speaker")}
    if not has_speaker_col or len(speakers) <= 1:
        shuffled = list(kept_records)
        rng.shuffle(shuffled)
        n_dev = max(1, int(round(len(shuffled) * dev_frac))) if shuffled else 0
        return (
            shuffled[n_dev:],
            shuffled[:n_dev],
            {r["speaker"] for r in shuffled[:n_dev] if r.get("speaker")},
        )

    counts = Counter(r["speaker"] for r in kept_records)
    lo, hi = np.percentile(sorted(counts.values()), [20, 80])
    eligible = [s for s in speakers if lo <= counts[s] <= hi]
    rest = [s for s in speakers if s not in set(eligible)]
    rng.shuffle(eligible)
    rng.shuffle(rest)

    hours_by_spk = Counter()
    for r in kept_records:
        hours_by_spk[r.get("speaker") or ""] += float(r.get("duration") or 0.0)
    target_hours = dev_frac * sum(hours_by_spk.values()) / 3600.0

    dev_speakers = set()
    dev_hours = 0.0
    pool = eligible + rest  # prefer the percentile band, then anyone else
    for spk in pool:
        if len(dev_speakers) >= max_dev_speakers:
            break
        if len(dev_speakers) >= min_dev_speakers and dev_hours >= target_hours:
            break
        dev_speakers.add(spk)
        dev_hours += hours_by_spk[spk] / 3600.0

    train = [r for r in kept_records if r.get("speaker") not in dev_speakers]
    dev = [r for r in kept_records if r.get("speaker") in dev_speakers]
    return train, dev, dev_speakers


def write_tsvs(out_dir: Path, train: list, dev: list) -> None:
    for name, rows in (("custom_train.tsv", train), ("custom_dev.tsv", dev)):
        with open(out_dir / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(f"{r['id']}\t{r['text']}\t{r['wav']}\n")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, help="manifest.jsonl path")
    p.add_argument("--dev-frac", type=float, default=0.01)
    p.add_argument("--min-dev-speakers", type=int, default=3)
    p.add_argument("--max-dev-speakers", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.manifest)
    out_dir = manifest_path.parent

    records = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    kept = [r for r in records if r.get("kept")]
    has_speaker_col = any(r.get("speaker") for r in kept)

    train, dev, dev_speakers = split_train_dev(
        kept,
        dev_frac=args.dev_frac,
        min_dev_speakers=args.min_dev_speakers,
        max_dev_speakers=args.max_dev_speakers,
        seed=args.seed,
        has_speaker_col=has_speaker_col,
    )
    write_tsvs(out_dir, train, dev)

    stats_path = out_dir / "stats.json"
    stats = {}
    if stats_path.is_file():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
    stats.update(
        {
            "train_samples": len(train),
            "dev_samples": len(dev),
            "dev_speakers": sorted(dev_speakers) if has_speaker_col else [],
            "hours_train": round(
                sum(float(r.get("duration") or 0.0) for r in train) / 3600.0, 4
            ),
            "hours_dev": round(
                sum(float(r.get("duration") or 0.0) for r in dev) / 3600.0, 4
            ),
        }
    )
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"[resplit] train={len(train)} dev={len(dev)} "
        f"dev_speakers={len(dev_speakers)} -> {out_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
