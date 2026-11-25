#!/usr/bin/env python3
"""Import human-approved voice candidates into the ViZipVoice voices dir.

Reads candidates.csv (produced by select_voice_candidates.py) and imports rows
whose ``approve`` cell is 1/yes/x. Each import writes:

  <voices-dir>/vi-{bac|trung|nam}-{nam|nu}-{name}-tunhien-v1.wav
  <voices-dir>/vi-{bac|trung|nam}-{nam|nu}-{name}-tunhien-v1.txt
  <voices-dir>/vi-{bac|trung|nam}-{nam|nu}-{name}-tunhien-v1.json

Required CSV fields per approved row: ``region`` (bac/trung/nam, diacritics or
english ok), ``name`` ([a-z0-9]+) and ``gender`` (male/female). Existing files
are never overwritten unless --force is given.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vizipvoice_addons.voice_meta import (  # noqa: E402
    normalize_gender,
    normalize_region,
    read_sidecar,
    write_sidecar,
)

_APPROVED = {"1", "yes", "x", "true", "y"}
_REGION_TO_TOKEN = {"Bắc": "bac", "Trung": "trung", "Nam": "nam"}
_GENDER_TO_TOKEN = {"male": "nam", "female": "nu"}
_NAME_RE = re.compile(r"^[a-z0-9]+$")


def _load_dataset_meta(csv_path: Path) -> dict:
    """Best-effort dataset/license lookup from ../stats.json."""
    stats = csv_path.parent.parent / "stats.json"
    try:
        if stats.is_file():
            data = json.loads(stats.read_text(encoding="utf-8"))
            return {"dataset": data.get("dataset", ""), "license": data.get("license", "")}
    except (OSError, ValueError):
        pass
    return {"dataset": "", "license": ""}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates", required=True, help="candidates.csv path")
    p.add_argument("--voices-dir", default="D:/TTS/voices")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.add_argument("--source-dataset", default=None)
    p.add_argument("--license", default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    csv_path = Path(args.candidates)
    voices_dir = Path(args.voices_dir)
    meta = _load_dataset_meta(csv_path)
    source_dataset = args.source_dataset or meta["dataset"]
    license_ = args.license or meta["license"]

    imported, skipped, rejected = 0, 0, []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for lineno, row in enumerate(reader, start=2):
            if (row.get("approve") or "").strip().lower() not in _APPROVED:
                continue
            speaker = (row.get("speaker") or "").strip()
            region_canon = normalize_region(row.get("region"))
            gender_canon = normalize_gender(row.get("gender"))
            name = (row.get("name") or "").strip().lower()
            if not region_canon:
                rejected.append((lineno, speaker, "missing/invalid region"))
                continue
            if not gender_canon:
                rejected.append((lineno, speaker, "missing/invalid gender"))
                continue
            if not _NAME_RE.match(name):
                rejected.append((lineno, speaker, "name must match [a-z0-9]+"))
                continue
            wav_src = Path(row.get("wav") or "")
            if not wav_src.is_file():
                rejected.append((lineno, speaker, f"wav not found: {wav_src}"))
                continue

            voice_name = (
                f"vi-{_REGION_TO_TOKEN[region_canon]}-"
                f"{_GENDER_TO_TOKEN[gender_canon]}-{name}-tunhien-v1"
            )
            dest_wav = voices_dir / f"{voice_name}.wav"
            dest_txt = voices_dir / f"{voice_name}.txt"
            dest_json = voices_dir / f"{voice_name}.json"
            existing = [p for p in (dest_wav, dest_txt, dest_json) if p.exists()]
            if existing and not args.force:
                skipped += 1
                print(
                    f"[skip] {voice_name}: exists ({existing[0].name}); use --force",
                    file=sys.stderr,
                )
                continue

            voices_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(wav_src, dest_wav)
            text = (row.get("text") or "").strip()
            if not text and wav_src.with_suffix(".txt").is_file():
                text = wav_src.with_suffix(".txt").read_text(encoding="utf-8").strip()
            dest_txt.write_text(text, encoding="utf-8")

            sidecar = read_sidecar(dest_json)
            sidecar.update(
                {
                    "gender": gender_canon,
                    "region": region_canon,
                    "source_dataset": source_dataset,
                    "license": license_,
                    "speaker": speaker,
                    "duration": row.get("duration") or None,
                    "cer": row.get("cer") or None,
                    "snr": row.get("snr") or None,
                    "dnsmos": row.get("dnsmos") or None,
                    "median_f0_hz": row.get("median_f0_hz") or None,
                    "transcript_source": "dataset",
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            write_sidecar(dest_json, sidecar)
            imported += 1
            print(f"[import] {voice_name} <- {wav_src.name}", file=sys.stderr)

    print(
        f"[done] imported={imported} skipped_existing={skipped} rejected={len(rejected)}",
        file=sys.stderr,
    )
    for lineno, speaker, why in rejected:
        print(f"  row {lineno} ({speaker}): {why}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
