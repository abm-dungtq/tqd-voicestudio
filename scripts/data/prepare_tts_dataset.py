#!/usr/bin/env python3
"""Prepare a HuggingFace dataset for ViZipVoice fine-tuning.

Streams or loads a dataset, decodes audio bytes with soundfile (no torchcodec),
normalises Vietnamese text, filters by duration / estimated SNR / DNSMOS / ASR
CER and token-vocab coverage, then writes:

  <out-dir>/wavs/<id>.wav          mono 24 kHz PCM16 (kept samples only)
  <out-dir>/manifest.jsonl        one line per processed sample
  <out-dir>/custom_train.tsv      {id}\t{text}\t{abs wav path}  (run_finetune.sh format)
  <out-dir>/custom_dev.tsv        held-out speakers (or a dev-frac random split)
  <out-dir>/stats.json            hours, reject counts, speakers, license

The run is streaming-safe and resumable: manifest lines and wavs are written
as soon as each sample's fate is final (never held in memory), and ``--resume``
skips samples already present in manifest.jsonl.
"""

from __future__ import annotations

import argparse
import glob as glob_mod
import io
import json
import queue
import sys
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_SR = 24000


def load_token_vocab(tokens_path: str) -> set:
    """Return the set of tokens in a ZipVoice tokens.txt file."""
    vocab = set()
    with open(tokens_path, "r", encoding="utf-8") as f:
        for line in f:
            token = line.rstrip("\n").split("\t")[0]
            if token:
                vocab.add(token)
    return vocab


def oov_chars(text: str, vocab: set) -> set:
    """Characters in ``text`` missing from ``vocab``. Spaces are allowed even
    when the vocab has no explicit space token."""
    return {c for c in text if c not in vocab and not c.isspace()}


def estimate_snr_db(wav: np.ndarray, sr: int, frame_ms: float = 20.0) -> float:
    """SNR proxy: dB difference between the 95th and 10th percentile frame energy."""
    wav = np.asarray(wav, dtype=np.float64).reshape(-1)
    frame = max(int(sr * frame_ms / 1000.0), 1)
    n = wav.size // frame
    if n < 2:
        return 0.0
    frames = wav[: n * frame].reshape(n, frame)
    energy = np.mean(frames * frames, axis=1) + 1e-12
    p95 = np.percentile(energy, 95)
    p10 = np.percentile(energy, 10)
    return float(10.0 * np.log10(p95 / p10))


def normalize_text(text: str) -> str:
    """NFC + the same Vietnamese normaliser used at inference time."""
    from zipvoice.vizipvoice import normalize_vietnamese_text

    return normalize_vietnamese_text(
        unicodedata.normalize("NFC", text.strip()), enabled=True
    )


def resample_mono_24k(wav: np.ndarray, sr: int) -> np.ndarray:
    import torch
    import torchaudio

    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    t = torch.from_numpy(np.asarray(wav, dtype=np.float32)).unsqueeze(0)
    if sr != TARGET_SR:
        t = torchaudio.functional.resample(t, sr, TARGET_SR)
    return t.squeeze(0).numpy()


def _decode_audio(sample: dict, audio_col: str):
    """Decode an Audio(decode=False) cell -> (float32 waveform, sample rate)."""
    cell = sample[audio_col]
    raw = None
    if isinstance(cell, dict):
        raw = cell.get("bytes")
        if raw is None and cell.get("path"):
            raw = Path(cell["path"]).read_bytes()
    elif isinstance(cell, (bytes, bytearray)):
        raw = bytes(cell)
    if raw is None:
        raise ValueError(f"cannot decode audio column {audio_col!r}: {type(cell)}")
    data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    return np.asarray(data, dtype=np.float32), int(sr)


def _write_pcm16(path: Path, wav: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.rint(wav * 32767.0), -32768, 32767).astype("<i2")
    sf.write(str(path), pcm, sr, subtype="PCM_16")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True, help="HF dataset name, e.g. qhuy242/VNAVC")
    p.add_argument("--config", default=None, help="dataset config name")
    p.add_argument("--split", default="train")
    p.add_argument(
        "--data-files",
        default=None,
        help="local parquet glob/dir: bypasses the hub and --streaming",
    )
    p.add_argument("--text-col", default="text")
    p.add_argument("--audio-col", default="audio")
    p.add_argument("--speaker-col", default=None)
    p.add_argument("--gender-col", default=None)
    p.add_argument("--dnsmos-col", default=None)
    p.add_argument("--streaming", action="store_true")
    p.add_argument("--max-samples", type=int, default=None)
    p.add_argument(
        "--resume",
        action="store_true",
        help="skip samples already present in manifest.jsonl",
    )
    p.add_argument("--out-dir", required=True)
    p.add_argument("--tokens", default="D:/TTS/models/ViZipvoice/tokens.txt")
    p.add_argument("--min-dur", type=float, default=1.0)
    p.add_argument("--max-dur", type=float, default=20.0)
    p.add_argument("--min-snr", type=float, default=15.0)
    p.add_argument(
        "--min-dnsmos",
        type=float,
        default=3.2,
        help="only applied when --dnsmos-col is given",
    )
    p.add_argument(
        "--asr-filter",
        action="store_true",
        help="transcribe kept candidates and reject on CER",
    )
    p.add_argument("--max-cer", type=float, default=0.10)
    p.add_argument("--asr-batch-size", type=int, default=16)
    p.add_argument(
        "--dev-speakers",
        type=int,
        default=3,
        help="minimum held-out speakers for custom_dev.tsv",
    )
    p.add_argument("--dev-frac", type=float, default=0.01)
    p.add_argument("--max-dev-speakers", type=int, default=20)
    p.add_argument("--license", default="")
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args(argv)


def load_ds(args):
    """Load the dataset (hub name or local parquet via --data-files)."""
    from datasets import Audio, load_dataset

    if args.data_files:
        path = Path(args.data_files)
        if path.is_dir() and (path / "dataset_info.json").is_file():
            from datasets import load_from_disk

            obj = load_from_disk(str(path))
            ds = obj[args.split] if hasattr(obj, "keys") else obj
        else:
            if path.is_dir():
                files = sorted(str(f) for f in path.rglob("*.parquet"))
            else:
                files = sorted(glob_mod.glob(args.data_files))
            if not files:
                raise FileNotFoundError(f"no parquet files matched {args.data_files!r}")
            ds = load_dataset("parquet", data_files=files, split=args.split)
    else:
        ds = load_dataset(
            args.dataset,
            args.config,
            split=args.split,
            streaming=args.streaming,
        )
    return ds.cast_column(args.audio_col, Audio(decode=False))


def derive_sample_id(sample: dict, dataset: str, n_seen: int) -> str:
    """Stable id from the dataset's own key columns when present."""
    raw = None
    for col in ("_id", "__key__", "id", "utt_id"):
        val = sample.get(col)
        if val is not None and str(val).strip():
            raw = str(val).strip()
            break
    prefix = dataset.split("/")[-1].lower()
    if raw is None:
        return f"{prefix}-{n_seen:06d}"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw)
    return f"{prefix}-{safe}"


def _process_sample(sample: dict, args, vocab: set, row_idx: int) -> dict:
    """All non-ASR checks for one sample. On pre-ASR acceptance the decoded
    waveform is kept under ``_wav`` so it can be written or ASR-checked."""
    rec = {
        "id": derive_sample_id(sample, args.dataset, row_idx),
        "row": row_idx,
        "speaker": str(sample.get(args.speaker_col, "") or "")
        if args.speaker_col
        else "",
        "gender": str(sample.get(args.gender_col, "") or "")
        if args.gender_col
        else "",
        "duration": 0.0,
        "snr": None,
        "dnsmos": None,
        "cer": None,
        "asr_text": None,
        "text": "",
        "wav": "",
        "kept": False,
        "reject_reason": None,
    }
    try:
        wav, sr = _decode_audio(sample, args.audio_col)
        wav24 = resample_mono_24k(wav, sr)
    except Exception as exc:
        rec["reject_reason"] = f"decode_error:{exc}"
        return rec

    dur = wav24.size / float(TARGET_SR)
    rec["duration"] = round(dur, 3)
    if dur < args.min_dur or dur > args.max_dur:
        rec["reject_reason"] = "duration"
        return rec

    try:
        text = normalize_text(str(sample.get(args.text_col, "")))
    except Exception as exc:
        rec["reject_reason"] = f"normalize_error:{exc}"
        return rec
    rec["text"] = text
    if not text:
        rec["reject_reason"] = "empty_text"
        return rec
    oov = oov_chars(text, vocab)
    if oov:
        rec["reject_reason"] = "oov:" + "".join(sorted(oov))
        return rec

    snr = estimate_snr_db(wav24, TARGET_SR)
    rec["snr"] = round(snr, 2)
    if snr < args.min_snr:
        rec["reject_reason"] = "low_snr"
        return rec

    if args.dnsmos_col:
        dnsmos = sample.get(args.dnsmos_col)
        rec["dnsmos"] = float(dnsmos) if dnsmos is not None else None
        if rec["dnsmos"] is not None and rec["dnsmos"] < args.min_dnsmos:
            rec["reject_reason"] = "low_dnsmos"
            return rec

    rec["kept"] = True
    rec["_wav"] = wav24
    return rec


def _finalise_record(rec: dict, wavs_dir: Path, mf) -> None:
    """Write the wav (kept only) + manifest line, then drop the waveform.
    The record is complete once written, so a crash loses at most the
    currently pending ASR batch."""
    wav_arr = rec.pop("_wav", None)
    if rec["kept"] and wav_arr is not None:
        wav_path = wavs_dir / f"{rec['id']}.wav"
        _write_pcm16(wav_path, wav_arr, TARGET_SR)
        rec["wav"] = str(wav_path.resolve())
    elif rec["kept"]:
        rec["kept"] = False
        rec["reject_reason"] = "no_audio"
    mf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    mf.flush()


def _run_asr_gate(pending: list, batch_size: int, max_cer: float) -> None:
    """Transcribe pending records (longest first); flip kept=False for high
    CER / ASR errors. Waveforms stay on the records until finalisation."""
    if not pending:
        return
    from vizipvoice_addons.asr import cer, normalize_for_compare, transcribe
    from zipvoice.vizipvoice import normalize_vietnamese_text

    def _cmp(t: str) -> str:
        return normalize_for_compare(
            t, normalizer=lambda x: normalize_vietnamese_text(x, enabled=True)
        )

    pending.sort(key=lambda r: r["duration"], reverse=True)
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        try:
            hyps = transcribe(
                [r["_wav"] for r in batch], TARGET_SR, batch_size=batch_size
            )
        except Exception as exc:
            print(f"[asr] batch failed: {exc}; marking batch as asr_error",
                  file=sys.stderr)
            hyps = [None] * len(batch)
        for rec, hyp in zip(batch, hyps):
            rec["asr_text"] = hyp
            if hyp is None:
                rec["cer"] = None
                rec["kept"] = False
                rec["reject_reason"] = "asr_error"
                continue
            c = cer(_cmp(rec["text"]), _cmp(hyp))
            rec["cer"] = round(c, 4)
            if c > max_cer:
                rec["kept"] = False
                rec["reject_reason"] = "high_cer"


def _read_manifest(path: Path):
    """Return (all ids, kept ids, set of 'row' values) from an existing manifest."""
    ids, kept_ids, rows = set(), set(), set()
    if not path.is_file():
        return ids, kept_ids, rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ids.add(rec["id"])
            if rec.get("kept"):
                kept_ids.add(rec["id"])
            if rec.get("row") is not None:
                rows.add(rec["row"])
    return ids, kept_ids, rows


def _cleanup_orphan_wavs(wavs_dir: Path, kept_ids: set) -> int:
    """Delete wav files whose id is not a kept manifest entry."""
    removed = 0
    if not wavs_dir.is_dir():
        return 0
    for wav in wavs_dir.glob("*.wav"):
        if wav.stem not in kept_ids:
            wav.unlink()
            removed += 1
    return removed


def finalize_from_manifest(manifest_path: Path, out_dir: Path, args) -> dict:
    """Build custom_train.tsv / custom_dev.tsv / stats.json from manifest.jsonl."""
    from resplit import split_train_dev, write_tsvs

    records = []
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    kept_records = [r for r in records if r.get("kept")]
    reject_counts = Counter(
        r.get("reject_reason") or "unknown" for r in records if not r.get("kept")
    )
    total_dur = sum(float(r.get("duration") or 0.0) for r in records)
    kept_dur = sum(float(r.get("duration") or 0.0) for r in kept_records)

    train, dev, dev_speakers = split_train_dev(
        kept_records,
        dev_frac=args.dev_frac,
        min_dev_speakers=args.dev_speakers,
        max_dev_speakers=args.max_dev_speakers,
        seed=args.seed,
        has_speaker_col=bool(args.speaker_col),
    )
    write_tsvs(out_dir, train, dev)

    speakers = {r["speaker"] for r in kept_records if r.get("speaker")}
    stats = {
        "dataset": args.dataset,
        "config": args.config,
        "split": args.split,
        "license": args.license,
        "samples_processed": len(records),
        "samples_kept": len(kept_records),
        "hours_total": round(total_dur / 3600.0, 4),
        "hours_kept": round(kept_dur / 3600.0, 4),
        "reject_counts": dict(reject_counts),
        "num_speakers": len(speakers),
        "dev_speakers": sorted(dev_speakers) if args.speaker_col else [],
        "train_samples": len(train),
        "dev_samples": len(dev),
        "hours_train": round(
            sum(float(r.get("duration") or 0.0) for r in train) / 3600.0, 4
        ),
        "hours_dev": round(
            sum(float(r.get("duration") or 0.0) for r in dev) / 3600.0, 4
        ),
    }
    with open(out_dir / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


def main(argv=None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    wavs_dir = out_dir / "wavs"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.jsonl"
    vocab = load_token_vocab(args.tokens)

    done_ids, kept_ids, done_rows = (
        _read_manifest(manifest_path) if args.resume else (set(), set(), set())
    )
    removed = _cleanup_orphan_wavs(wavs_dir, kept_ids)
    if args.resume:
        print(
            f"[resume] {len(done_ids)} samples already in manifest "
            f"({removed} orphan wavs removed)",
            file=sys.stderr,
        )
    elif removed:
        print(f"[clean] {removed} stale wavs removed", file=sys.stderr)

    ds = load_ds(args)
    skip_by_id = True
    if args.resume and done_rows:
        # The manifest is a contiguous row prefix only when no pending ASR
        # batch was lost mid-write; then ds.skip() is valid and much cheaper.
        n_done = len(done_rows)
        if done_rows == set(range(1, n_done + 1)) and args.streaming:
            ds = ds.skip(n_done)
            skip_by_id = False
            print(f"[resume] contiguous prefix: ds.skip({n_done})", file=sys.stderr)

    mf = open(manifest_path, "a" if args.resume else "w", encoding="utf-8")
    n_new = 0
    row_base = len(done_rows) if not skip_by_id else 0
    t0 = time.perf_counter()
    t_asr = 0.0
    t_asr_lock = threading.Lock()

    def note_asr(dt: float, n_clips: int) -> None:
        nonlocal t_asr
        with t_asr_lock:
            t_asr += dt
        print(
            f"[asr] gated {n_clips} clips in {dt:.1f}s "
            f"({n_clips / dt * 60:.0f}/min)",
            file=sys.stderr,
        )

    # Three stages so the GPU never waits on decoding:
    #   producer thread  -> work_q   (iterate, decode, pre-ASR checks)
    #   main thread      -> asr_in   (kept recs) ; writes manifest + wavs
    #   asr worker       -> done_q   (gated recs)
    work_q: queue.Queue = queue.Queue(maxsize=128)
    asr_in: queue.Queue = queue.Queue(maxsize=256)
    done_q: queue.Queue = queue.Queue()
    stop = object()
    asr_done = object()

    def produce():
        try:
            for n, sample in enumerate(ds, start=1):
                if args.max_samples is not None and n > args.max_samples:
                    break
                row_idx = row_base + n
                if skip_by_id and derive_sample_id(
                    sample, args.dataset, row_idx
                ) in done_ids:
                    continue
                work_q.put(_process_sample(sample, args, vocab, row_idx))
        except Exception as exc:  # pragma: no cover - iterator errors
            work_q.put(exc)
        finally:
            work_q.put(stop)

    def asr_worker():
        pending: list = []
        while True:
            try:
                item = asr_in.get(timeout=0.3)
            except queue.Empty:
                item = None
            if item is stop:
                break
            if item is not None:
                pending.append(item)
            # Gate eagerly whenever a full batch is ready; a short timeout
            # also drains the tail so the queue never stalls.
            if pending and (len(pending) >= args.asr_batch_size * 2 or item is None):
                n_pending = len(pending)
                t = time.perf_counter()
                _run_asr_gate(pending, args.asr_batch_size, args.max_cer)
                note_asr(time.perf_counter() - t, n_pending)
                done_q.put(pending)
                pending = []
        if pending:
            n_pending = len(pending)
            t = time.perf_counter()
            _run_asr_gate(pending, args.asr_batch_size, args.max_cer)
            note_asr(time.perf_counter() - t, n_pending)
            done_q.put(pending)
        done_q.put(asr_done)

    def drain_done(final: bool = False) -> None:
        while True:
            try:
                item = done_q.get() if final else done_q.get_nowait()
            except queue.Empty:
                return
            if item is asr_done:
                return
            for rec in item:
                _finalise_record(rec, wavs_dir, mf)

    threads = [
        threading.Thread(target=produce, daemon=True),
        threading.Thread(target=asr_worker, daemon=True),
    ]
    for t in threads:
        t.start()

    producer_dead = False
    try:
        while True:
            try:
                item = work_q.get(timeout=0.3)
            except queue.Empty:
                drain_done()
                if producer_dead:
                    break
                continue
            if item is stop:
                producer_dead = True
                drain_done()
                break
            if isinstance(item, Exception):
                raise item
            rec = item
            n_new += 1
            if args.asr_filter and rec["kept"]:
                asr_in.put(rec)
            else:
                _finalise_record(rec, wavs_dir, mf)
            if n_new % 50 == 0:
                drain_done()
                el = time.perf_counter() - t0
                print(
                    f"[prep] processed {n_new} samples "
                    f"({n_new / el * 60:.0f}/min, asr {t_asr:.0f}s of {el:.0f}s)",
                    file=sys.stderr,
                )
        # Tell the ASR worker there is no more input, then drain it fully.
        asr_in.put(stop)
        drain_done(final=True)
    finally:
        # Pending (not yet ASR-gated) rows are deliberately dropped on crash:
        # their ids stay out of the manifest, so --resume reprocesses them.
        mf.close()

    stats = finalize_from_manifest(manifest_path, out_dir, args)
    print(
        f"[done] {stats['samples_kept']}/{stats['samples_processed']} kept "
        f"({stats['hours_kept']}h of {stats['hours_total']}h) -> {out_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
