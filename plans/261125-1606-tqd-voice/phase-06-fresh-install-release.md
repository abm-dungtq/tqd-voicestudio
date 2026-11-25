---
phase: 6
title: "Kiểm tra cài mới + phát hành v0.1.0"
status: pending
priority: P1
effort: "3h"
dependencies: [2, 4, 5]
---

> Cwd mặc định: `D:\TTS\tqd-voice` (worktree). `$PY` = `D:/TTS/ViZipvoice/.venv/Scripts/python.exe`. Không sửa file trong `D:\TTS\ViZipvoice`.

# Phase 6: Kiểm tra cài mới + phát hành v0.1.0

## Goal
Chứng minh bạn bè cài được từ GitHub và dùng được ngay: cài như một tool `uv` độc lập vào thư mục home trống, chạy đủ lệnh, thử văn bản ~2.700 từ, thử đường CPU, rồi viết README và phát hành tag `v0.1.0` trên `abm-dungtq/tqd-voice`.

## Context
- Tất cả push/tag/release/đổi nhánh mặc định là hành động bên ngoài → cook xin xác nhận người dùng trước Task 6.6.
- Fork của repo public là public.
- Model và code gốc: ZipVoice (k2-fsa, Apache-2.0), ViZipVoice (`iamdinhthuan/ViZipvoice`, model `contextboxai/ViZipvoice`, Apache-2.0).

## Files to Create / Modify
- Create: `NOTICE`, `docs/TQD_VOICE.md`
- Modify: `README.md` (thêm khối TQD-Voice ở đầu file, giữ nguyên nội dung cũ bên dưới)

## Tasks

### Task 6.1 — Tài liệu
- Steps:
  1. `README.md`: chèn ở đầu một khối tiếng Việt ≤ 60 dòng: TQD-Voice là gì; yêu cầu (Windows/Linux, NVIDIA ≥ 6 GB khuyến nghị, CPU chạy được nhưng chậm, ~3 GB ổ đĩa); cài một lệnh (`irm https://raw.githubusercontent.com/abm-dungtq/tqd-voice/v0.1.0/install.ps1 | iex` cho Windows PowerShell, `curl -LsSf https://raw.githubusercontent.com/abm-dungtq/tqd-voice/v0.1.0/install.sh | bash` cho Linux) và cài thủ công 3 lệnh; ví dụ `say`, `clone`, `voices`; MCP (`tqd-voice mcp-config`); xử lý sự cố (`tqd-voice doctor`); dòng "Chỉ clone giọng khi người đó đồng ý"; link `docs/TQD_VOICE.md`.
  2. `docs/TQD_VOICE.md`: bảng lệnh đầy đủ với mọi tham số đã cài ở phase 3–5; biến môi trường (`TQD_VOICE_HOME`, `TQD_CHUNKER`, `VIZIPVOICE_*`); cấu trúc `~/.tqd-voice`; kết quả eval chunker (sao chép nguyên `D:/TTS/outputs/chunker_eval/v2/compare.txt`); thời gian bài dài từ Task 6.3.
  3. `NOTICE`: TQD-Voice dựa trên ZipVoice (Copyright Xiaomi Corp. / k2-fsa, Apache-2.0 — lấy dòng copyright thật từ header các file `zipvoice/*.py`) và ViZipVoice (iamdinhthuan, Apache-2.0); model tải từ `contextboxai/ViZipvoice` (Apache-2.0), không kèm trong repo.
- Verify: `grep -c "tqd-voice doctor" README.md` ≥ 1; `test -f NOTICE && test -f docs/TQD_VOICE.md` exit 0.

### Task 6.2 — Cài như tool độc lập từ mã nguồn local
- Steps:
  1. `uv tool install --python 3.12 --torch-backend=auto --reinstall D:/TTS/tqd-voice`
  2. Đặt `TQD_VOICE_HOME=D:/TTS/tqd-fresh-home` (thư mục chưa tồn tại), `PYTHONUTF8=1`; KHÔNG đặt `HF_HOME`, `VIZIPVOICE_MODEL_DIR`, `VIZIPVOICE_VOICES_DIR`.
  3. Chạy lần lượt bằng exe của uv tool (`uv tool dir --bin` cho biết thư mục): `tqd-voice doctor`, `tqd-voice install`, `tqd-voice say "Bạn bè cài đặt thành công." -o D:/TTS/tqd-fresh-home/t1.wav`, `tqd-voice clone friendtest D:/TTS/voices/vi-bac-nam-ducanh-tunhien-v1.wav`, `tqd-voice voices`, `$PY scripts/smoke/mcp_smoke.py --exe "<đường dẫn tqd-voice của uv tool>"`, `tqd-voice stop`.
- Success criteria / Verify: mọi lệnh exit 0; `t1.wav` > 10000 byte; `voices` liệt kê `friendtest`; smoke in `MCP_SMOKE_OK`; `python -c "import importlib.metadata as m; print(m.version('tqd-voice'))"` chạy bằng python của uv tool (`uv tool dir`/`tqd-voice/Scripts/python.exe`) in `0.1.0`.

### Task 6.3 — Văn bản dài ~2.700 từ
- Steps:
  1. Tạo `D:/TTS/tmp/long.txt` = nối 5 lần toàn bộ `text` trong `plans/261125-1606-tqd-voice/assets/longform_texts.json`, các văn bản cách nhau bằng một dòng trống.
  2. Với `TQD_VOICE_HOME=D:/TTS/tqd-fresh-home`: đo thời gian `tqd-voice say -f D:/TTS/tmp/long.txt -o D:/TTS/tmp/long.wav` (dùng `time` của bash).
  3. Đo độ dài bằng `$PY -c "import soundfile as sf; i=sf.info('D:/TTS/tmp/long.wav'); print(round(i.duration,1))"`.
- Verify: lệnh exit 0; `wc -w D:/TTS/tmp/long.txt` ≥ 2600; độ dài âm thanh ≥ 600 giây; ghi `wall time` và `duration` vào `docs/TQD_VOICE.md`.

### Task 6.4 — Đường CPU
- Steps: tạo `D:/TTS/tqd-fresh-home/config.toml` bằng `tqd_voice.config.save` với `device="cpu", fp16=False, max_streams=1, port=8766` (giữ các trường khác mặc định); `tqd-voice say "Thử chạy bằng CPU." -o D:/TTS/tqd-fresh-home/cpu.wav`; `curl -s http://127.0.0.1:8766/health`; `tqd-voice stop`; sau đó khôi phục config cuda bằng `tqd-voice install --no-asr --no-smoke`.
- Verify: `cpu.wav` > 10000 byte; health chứa `vizipvoice-cpu-fp32`; sau khôi phục, `config.toml` chứa `device = "cuda"`.

### Task 6.5 — Bộ test đầy đủ
- Verify: `PYTHONUTF8=1 $PY -m pytest tests/addons tests/tts_service tests/tqd -q` chỉ fail đúng `tests/tts_service/test_models.py::test_generation_ranges_match_public_api_limits`.

### Task 6.6 — Phát hành (CẦN người dùng xác nhận trước)
- Steps:
  1. Commit "Docs and release prep for TQD-Voice v0.1.0" trên `tqd/v0.1`; quét bí mật như Task 1.3 bước 2.
  2. `git push tqd tqd/v0.1` và `git push tqd tqd/v0.1:master` (fast-forward nhánh mặc định của fork; nếu bị từ chối vì không fast-forward thì STOP theo Failure Protocol, không dùng `--force`).
  3. `git tag -a v0.1.0 -m "TQD-Voice v0.1.0"`; `git push tqd v0.1.0`.
  4. `gh release create v0.1.0 --repo abm-dungtq/tqd-voice --title "TQD-Voice v0.1.0" --notes-file docs/TQD_VOICE.md`.
  5. Kiểm tra cài từ GitHub: `uv tool install --reinstall --python 3.12 --torch-backend=auto "git+https://github.com/abm-dungtq/tqd-voice@v0.1.0"` rồi `tqd-voice doctor` với `TQD_VOICE_HOME=D:/TTS/tqd-fresh-home`.
- Verify: `gh release view v0.1.0 --repo abm-dungtq/tqd-voice --json tagName -q .tagName` in `v0.1.0`; lệnh cài bước 5 exit 0; `tqd-voice doctor` exit 0.

## Todo
- [ ] Task 6.1 — Tài liệu
- [ ] Task 6.2 — Cài như tool độc lập từ mã nguồn local
- [ ] Task 6.3 — Văn bản dài ~2.700 từ
- [ ] Task 6.4 — Đường CPU
- [ ] Task 6.5 — Bộ test đầy đủ
- [ ] Task 6.6 — Phát hành (CẦN người dùng xác nhận trước)

## Failure Protocol
If any Verify step does not meet its stated pass condition, STOP this phase.
Do not improvise a fix, retry blindly, or reason around the failure.
Spawn the `kongming` subagent for next-step counsel and pass:
- the phase and task id,
- what you attempted (the steps you ran),
- the exact command and its full output,
- the pass condition it failed to meet.
Apply kongming's guidance, then re-run the Verify step.
If `kongming` cannot be spawned in this environment, STOP and report the same
failure evidence to the user. Never continue by self-reasoning.
