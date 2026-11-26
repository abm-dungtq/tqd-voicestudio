# TQD-VoiceStudio v0.3.0 — design spec (authoritative)

> **Amendment 2026-11-26 (user approved):** studio model FAILED the fixed gate (30 voices × 24 sentences: base CER 0.0090 vs studio 0.0126). Done-means 6 and feature 11 are DROPPED: no studio upload, no `model=studio`, no `--model studio`, no Gốc/Studio selector. The VIVOS pack ships **22 voices**: drop `vi-bac-nu-hoaian-tunhien-v1` (InfoRe, licence unknown) and `vi-bac-nu-thanhvan-tintuc-v1` (VAIS-1000, licence unknown); card lists per-voice licence (17 VIVOS CC BY-NC-SA 4.0, 5 DNU-ViSpeech CC BY-NC 4.0); pack licence `cc-by-nc-sa-4.0`. Sections below that mention the studio model are superseded.

Tracking issue: https://github.com/abm-dungtq/tqd-voicestudio/issues/1
Branch: `tqd/v0.3` in worktree `D:\TTS\tqd-voice`. Never edit `D:\TTS\ViZipvoice`; never restart port 8001.
Commit author: `-c user.name="DũngTQ - 0976202028" -c user.email="241233497+abm-dungtq@users.noreply.github.com"`.

## Global rules
- Server default bind 127.0.0.1. Non-loopback bind requires an API key (refuse to start otherwise).
- Base model is the default everywhere. Studio model only on explicit request and only for preset (studio) voices.
- Clone always requires consent (`consent=true` form field; API returns 400 without it; CLI `--consent` flag; MCP `create_voice(consent: bool)`).
- All new persistent state lives under `paths.home()`; SQLite file `paths.home()/studio.db` (add `paths.db_path()`).
- No GPL/AGPL runtime deps. Check the licence of every new dep and list it in NOTICE.
- New unit tests must not need the real model (fake engine / pure functions). Real-model checks are smoke scripts.
- User-facing strings in Vietnamese.

## H1 — licence, studio model, VIVOS pack
1. Licence:
   - `LICENSE` = verbatim PolyForm Noncommercial 1.0.0 (from https://polyformproject.org/licenses/noncommercial/1.0.0/), required notice line `Required Notice: Copyright DũngTQ - 0976202028 (https://github.com/abm-dungtq/tqd-voicestudio)`.
   - Move the old Apache text to `LICENSE-APACHE-2.0`. NOTICE: upstream ZipVoice (Xiaomi) and ViZipVoice (iamdinhthuan) files remain Apache-2.0 with their headers; TQD-VoiceStudio modifications and the combined work are PolyForm NC 1.0.0; model/voice pack licences.
   - pyproject `license = { text = "PolyForm-Noncommercial-1.0.0" }`. README top: "Chỉ dùng phi thương mại".
2. Studio model:
   - Source: `D:/TTS/models/ViZipvoice-ft10k/{checkpoint-10000.pt,config.json,tokens.txt}`. Convert weights to `model.safetensors` (inference weights only); verify `ViZipVoiceTTS` loads it and produces audio.
   - Eval (fixed, authored by lead): testset `D:/TTS/outputs/studio30/testset.json` (30 studio voices × 24 FIXED_SENTENCES = 720 utts). Run
     `scripts/eval/eval_tts.py run --model-dir D:/TTS/models/ViZipvoice --checkpoint-name checkpoint-1860000.pt --testset D:/TTS/outputs/studio30/testset.json --out-dir D:/TTS/outputs/studio30/base`
     and the same with `--model-dir D:/TTS/models/ViZipvoice-ft10k --checkpoint-name checkpoint-10000.pt --out-dir D:/TTS/outputs/studio30/studio`.
     Decision: Done-means 6 holds iff `studio.mean_cer < base.mean_cer` on group `studio`. Do not change testset, seed, num_step or the rule. If it fails, STOP and report.
   - Upload to HF `dungtq/tqd-voicestudio-studio` (exists): `model.safetensors`, `config.json`, `tokens.txt`, `README.md` model card (license `cc-by-nc-4.0`, base_model `contextboxai/ViZipvoice`, datasets VieNeu-TTS-140h CC BY-NC 4.0 + YodaLingua CC BY 4.0, both eval tables, "chỉ dùng cho 30 giọng studio; giọng clone dùng model gốc", non-commercial). Pin the resulting commit sha in code.
3. VIVOS pack:
   - Upload to HF dataset `dungtq/tqd-voicestudio-vivos-voices` (exists): `library/<id>/{ref.wav,ref.txt,VOICE-CARD.md}` for all 24 voices in `D:/TTS/voice-share-vivos-17/voice-share-vivos-17/library` (skip ref.m4a), `GIAY-PHEP.md`, `voices.csv`, `README.md` dataset card (license `cc-by-nc-sa-4.0`, VIVOS attribution, no-impersonation rule). Pin sha.
4. Install:
   - `tqd-voicestudio install --model studio` downloads the studio repo to `paths.home()/models/ViZipvoice-studio`; `--voices vivos` downloads the pack to `paths.home()/packs/vivos` and sets config `library_dir`. Both print the licence notice and require confirmation: interactive y/N, or `--yes` when non-interactive. Flags combinable and usable on an already-installed home (skip base download if present).
   - Config gains `library_dir: str = ""` → env `VIZIPVOICE_LIBRARY_DIR`; `studio_model_dir` derived from `paths.studio_model_dir()` (env override `TQD_STUDIO_MODEL_DIR`).
5. Server model selection (feature 11):
   - `SpeechRequest.model`: `"studio"` selects the studio model; any other value = base (OpenAI clients send arbitrary names).
   - Studio model lazily loaded on first use (shares the vocoder if feasible); 400 `"Chưa cài model studio: chạy tqd-voicestudio install --model studio"` if missing.
   - Non-preset voice with `model=studio` → synthesized with base; response header `X-TQD-Model: base|studio` always set.
   - `/v1/models` lists `studio` only when installed. `/health` adds `"studio_model": bool`.

## H2 — jobs, webhook, history/cache, ratings, audio features (API + tests)
- Jobs: `POST /v1/jobs` (SpeechRequest fields + `webhook_url?`, `subtitles: bool=true`) → 202 `{id,status}`; `GET /v1/jobs`, `GET /v1/jobs/{id}` → `{id,status: queued|running|done|failed|canceled, progress 0..1, segments_done, segments_total, error, created_at, finished_at, audio_url, srt_url, vtt_url, history_id}`; `GET /v1/jobs/{id}/audio`, `/subtitles.srt`, `/subtitles.vtt`; `POST /v1/jobs/{id}/cancel`.
  - One worker thread, FIFO. Segments: split at voice-run and paragraph (blank line) boundaries; paragraphs > 400 words split at sentence ends. Each segment rendered to `home/jobs/<id>/seg-NNNN.wav` + timing json; final concatenation applies the same joins/pauses as sync synthesis.
  - Restart: on startup, `running` jobs become `queued` and resume, skipping segments already on disk.
  - Webhook on done/failed/canceled: POST JSON `{event: "job.done"|"job.failed"|"job.canceled", job: {...}}`, header `X-TQD-Signature: sha256=<hex HMAC-SHA256(secret, raw body)>`, secret = config `webhook_secret` (generated at install/first use, `tqd-voicestudio key webhook` prints it). 3 attempts, backoff 1 s/4 s/16 s (injectable for tests). Delivery attempts recorded on the job.
- History + cache: every successful speech/timed/job output stored in `history` table + file under `home/history/`. Cache key = sha256 of (input, voice, speed, style, model, response_format, sample_rate, normalize, trim_silence, music id/params, cast, lexicon content hash, quality_check). Hit → stored bytes, header `X-TQD-Cache: hit` (`miss` otherwise). Request `cache: bool = true` to bypass. `GET /v1/history?limit=&offset=`, `GET /v1/history/{id}/audio`, `DELETE /v1/history/{id}`. Prune to config `history_max = 500`. Response header `X-TQD-History-Id`.
- Ratings: `POST /v1/ratings {history_id|job_id, segment_index?, rating: 1|-1, note?}` (stores text, voice, model with it); `GET /v1/ratings?format=json|jsonl`.
- `[pause:N]` (0 < N ≤ 10 seconds, float, `[pause:1.5]` / `[pause:2s]`): exact silence inserted; works with `[voice=…]` tags and roles; subtitle timings account for it. Pure parser in `vizipvoice_addons/pauses.py`.
- Roles (feature 1): `SpeechRequest.cast: Dict[str,str] | None`. When set, lines `Vai: lời` are mapped to `[voice=cast[Vai]]`; unknown role → 400 listing roles. Pure function `vizipvoice_addons/script_roles.py` (`parse_roles(text) -> list[str]`, `apply_cast(text, cast) -> str`). `POST /v1/script/roles {input}` → `{roles}`.
- Post-processing: `normalize: bool=false` → integrated loudness −16 LUFS (pyloudnorm, peak-limit −1 dBFS); `trim_silence: bool=false` → internal silences > 0.6 s shortened to 0.6 s (explicit `[pause]` silences preserved).
- Music: `POST /v1/music` (multipart upload, ≤ 50 MB, any ffmpeg/soundfile-readable) → `{id,name,duration}`; `GET /v1/music`; `DELETE /v1/music/{id}`. `SpeechRequest.music: {id, volume_db=-20, duck_db=-12, fade_s=2.0} | None`: music looped/trimmed to speech length + fade-out; ducked by `duck_db` while speech envelope is active (attack 50 ms, release 300 ms). Pure numpy mixer in `vizipvoice_addons/mixing.py`.
- Import: `POST /v1/import` multipart `file` (.txt .md .docx .pdf) or JSON `{url}` → `{title, text, source, words}`. md: strip code blocks, tables, images, HTML, link targets. URL: main article text (drop nav/ads). Optional extra `[import]` (python-docx, pypdf, trafilatura or equivalent non-GPL); missing deps → 501 with the exact `uv tool install` fix. Installers (`install.ps1`, `install.sh`) install `tqd-voicestudio[import]`.
- Subtitles (feature 2) and QC (feature 4) already exist: make sure jobs carry them and history stores srt/vtt.
- CLI additions: `say --normalize --trim-silence --music ID --model studio --job` (`--job` submits and polls with a progress line), `jobs [list|show ID|cancel ID]`, `import FILE_OR_URL [-o out.txt]`, `clone --consent` required.
- MCP stdio tools: keep `list_voices`, `synthesize_speech`, `create_voice` (+ `consent`), add `create_job`, `job_status`.

## H3 — remote access over Tailscale
- Config `host = "127.0.0.1"`, `api_key = ""`. `tqd-voicestudio key new` generates (secrets.token_urlsafe(32)), saves to config.toml, prints once. `serve --host H`; non-loopback host + empty key → exit 2 with Vietnamese message pointing to `key new`.
- When a key is set, every route except `GET /health` and the static UI shell requires `Authorization: Bearer <key>` (constant-time compare). Local CLI/MCP-stdio clients send the key from config automatically.
- MCP Streamable HTTP mounted at `/mcp` in the same process, same Bearer check, same tools as stdio (single tool definition shared). Verify the exact mcp 2.x API in the installed library.
- `mcp-config --remote URL` prints: Claude Code `claude mcp add --transport http …`, Cursor JSON (url + headers), and the stdio-bridge option (`tqd-voicestudio mcp` with `VIZIPVOICE_API_URL`/`VIZIPVOICE_API_KEY`) for Claude Desktop.
- `scripts/smoke/remote_mcp_smoke.py --url http://100.80.65.15:8765/mcp --key K` → asserts 401 without key, then list_voices + synthesize_speech OK, prints `REMOTE_MCP_OK`.
- `docs/REMOTE.md` (Vietnamese): bật server cho máy khác, tạo key, cấu hình client (Claude Code, Cursor, Claude Desktop qua stdio bridge, Web UI), kiểm tra kết nối (`curl /health`, smoke), lỗi thường gặp (firewall Windows, sai key, Tailscale offline, cổng bận), Tailscale HTTPS (`tailscale serve`) gợi ý.

## H4 — Web UI
- Static files in `tqd_voicestudio/web/` (index.html, app.js, app.css; vanilla, no build), served at `GET /` and `/static/*`; package-data included.
- Tabs: Đọc, Clone giọng, Thư viện giọng, Job, plus panels for Từ điển phát âm, Lịch sử, Nhạc nền, Đánh giá. Covers all 11 features: roles editor with cast pickers, SRT/VTT download, lexicon table editor, QC toggle + result, history list with replay/download/delete + cache badge, `[pause]` insert button, normalize/trim toggles, music upload/select/volume, import file/URL into the text box, 👍/👎 on results and job segments, model Gốc/Studio selector (Studio disabled when not installed or voice not studio).
- API key field (stored in localStorage) shown when the server requires one; audio fetched as blobs with the header.
- Clone form: consent checkbox required (button disabled until ticked).
- Playwright test (`tests/e2e/`, node devDependency in `tests/e2e/package.json`, not shipped) against a server with a fake/real engine; screenshots saved to `tests/e2e/artifacts/`.

## H5 — macOS + CI
- `VIZIPVOICE_DEVICE=auto` → cuda, else mps (set `PYTORCH_ENABLE_MPS_FALLBACK=1`, fp16 off), else cpu. doctor on Darwin: arm64 + mps available → mps; else cpu; no CUDA advice on mac; ffmpeg fix `brew install ffmpeg`.
- `install.sh` works on macOS (bash 3.2 compatible).
- `.github/workflows/ci.yml`: job `unit` (ubuntu, pytest of the model-free suites) and matrix `smoke` on ubuntu-latest, windows-latest, macos-14: `uv tool install --python 3.12 --torch-backend=cpu ".[import]"`, `doctor`, `install --no-asr --yes`, `say` → wav > 10 KB. Must be green on the v0.3.0 tag commit.

## H6 — release
- Version 0.3.0, installers/README/doctor hints → v0.3.0. Docs updated (README, docs/TQD_VOICESTUDIO.md, docs/MCP_INTEGRATION.md, docs/REMOTE.md, AGENTS.md).
- Full test suite: only the known `test_generation_ranges_match_public_api_limits` failure.
- Fresh install from local source on Windows + from the tag after publishing.
