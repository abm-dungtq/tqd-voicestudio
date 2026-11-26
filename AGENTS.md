# AGENTS.md — TQD-Voice / ViZipVoice dev notes

Repo: Vietnamese ZipVoice TTS (flow-matching + Vocos), packaged as the `tqd-voice` CLI.
All data directories are path-configurable via `tqd_voice/paths.py`: `TQD_VOICE_HOME`
(default `~/.tqd-voice`) plus per-directory overrides (`VIZIPVOICE_MODEL_DIR`,
`VIZIPVOICE_VOICES_DIR`, `VIZIPVOICE_CT2_MODEL`, `HF_HOME`, `VIZIPVOICE_LIBRARY_DIR`).

Production on this box (port 8001) runs from `D:\TTS\ViZipvoice` via `D:\TTS\start-api.cmd`,
which keeps the `D:\TTS` paths through env vars (`VIZIPVOICE_MODEL_DIR`, `VIZIPVOICE_VOICES_DIR`,
`VIZIPVOICE_CT2_MODEL`, `VIZIPVOICE_LIBRARY_DIR`, `TQD_CHUNKER=legacy`). Develop in the
worktree `D:\TTS\tqd-voice` (branch `tqd/v0.1`); never edit `D:\TTS\ViZipvoice`.

## Python env

Windows venv: `.venv\Scripts\python.exe` (Python 3.12, torch 2.8 cu128).
Install packages:

```bash
uv pip install --python .venv/Scripts/python.exe <pkg>
uv pip install --python .venv/Scripts/python.exe -r requirements-data.txt  # data toolkit
```

The dev venv (`D:/TTS/ViZipvoice/.venv`) has a `vizipvoice.pth` pointing at `D:\TTS\ViZipvoice`, so
run worktree code with `PYTHONPATH=D:/TTS/tqd-voice` (otherwise `vizipvoice_openai_server`
resolves to the production copy). Clean-environment checks use `uv tool install` instead.

Always set `PYTHONUTF8=1` (and `HF_HOME=D:/TTS/.hf-cache` for HF lookups) —
Vietnamese text will crash cp1252 consoles otherwise.

## Tests

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/addons tests/tts_service tests/tqd -q
```

`tests/addons/` covers the pure helpers in `vizipvoice_addons/` + data scripts.
Note: `tests/tts_service/test_models.py::test_generation_ranges_match_public_api_limits`
is a known pre-existing failure unrelated to the addons.

## Run the API server (dev)

```bash
HF_HOME=D:/TTS/.hf-cache PYTHONUTF8=1 PORT=8011 \
  .venv/Scripts/python.exe vizipvoice_openai_server.py
```

Port 8001 is used by the production instance, 8000 by VieNeu-TTS — use 8011+
for smoke tests. Auth is off unless `VIZIPVOICE_API_KEY` is set.

QC gate envs: `VIZIPVOICE_QUALITY_CHECK`, `VIZIPVOICE_QC_MAX_CER` (0.15),
`VIZIPVOICE_QC_MAX_RETRIES` (2), `VIZIPVOICE_ASR_MODEL` (vinai/PhoWhisper-small),
`VIZIPVOICE_LEXICON_PATH`.

## Data workflow (scripts/data/)

```bash
python scripts/data/prepare_tts_dataset.py --dataset <hf> [--speaker-col ...] \
  [--streaming] [--asr-filter] --out-dir <dir>
python scripts/data/select_voice_candidates.py --manifest <dir>/manifest.jsonl \
  --out-dir <dir>/candidates --top 10
python scripts/data/import_voices.py --candidates <dir>/candidates/candidates.csv \
  [--voices-dir D:/TTS/voices] [--force]
```

## Finetune

`egs/zipvoice/run_finetune_vi.sh` — tokenizer=simple. Vars: `train_tsv`,
`dev_tsv`, `model_dir` (local checkpoint dir; else downloads from HF
`contextboxai/ViZipvoice`), `python`, `num_iters`, `save_every`, `exp_dir`,
`stage`, `stop_stage`. Run from `egs/zipvoice/` (writes to `data/`, `exp/`,
`download/` there).
