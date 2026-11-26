---
title: "TQD-Voice v0.1 — CLI TTS tiếng Việt tự cài, có MCP"
description: "Biến ViZipVoice (model gốc 1860k) thành công cụ miễn phí chia sẻ qua GitHub: bạn bè cài bằng uv, doctor tự phân tích máy, chia đoạn theo nhịp 13–25 từ, MCP stdio cho agent."
status: in-progress
priority: P1
effort: 19h
branch: tqd/v0.1
tags: [feature, cli, mcp, tts, packaging]
blockedBy: []
blocks: []
created: 2026-11-25
---

# TQD-Voice v0.1

## Overview
Biến ViZipVoice (model gốc 1860k) thành công cụ miễn phí chia sẻ qua GitHub: bạn bè cài bằng `uv`, `tqd-voice doctor` tự phân tích máy, bộ chia đoạn gom văn bản thành đoạn 13–25 từ, và MCP stdio cho agent. Repo là fork của `iamdinhthuan/ViZipvoice` dưới tài khoản `abm-dungtq`, đổi tên thành `tqd-voice`.

## Contract (đã chốt với người dùng)
- **Outcome:** `uv tool install --torch-backend=auto git+https://github.com/abm-dungtq/tqd-voice@v0.1.0` rồi `tqd-voice install` → dùng được `say`, `clone`, `voices`, `serve`, `mcp` trên máy bạn bè.
- **Constraints:** chỉ model gốc `contextboxai/ViZipvoice` (Apache-2.0, revision `7903dcb99d5a17c2e59a0ef5f7db32de4bed357b`); server chỉ nghe `127.0.0.1`; giữ API kiểu OpenAI; production cổng 8001 trên máy này không bị đổi hành vi; mục tiêu chính Windows/Linux + NVIDIA ≥6 GB, CPU chạy được nhưng chậm.
- **Non-goals:** webhook, job nền, MCP từ xa, web UI mới, model fine-tune, Mac/Apple Silicon, nhiều engine, giọng riêng trong `D:\TTS\voices`.
- **Acceptance:** xem mục Success Criteria.
- **Better approach đã áp dụng:** gom đoạn 13–25 từ thay vì chia câu ngắn (eval 384 câu: 1–4 từ lỗi 50%, 13–25 từ lỗi 2,8%).

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Repo setup: fork, hygiene, đường dẫn cấu hình](./phase-01-start.md) | Completed |
| 2 | [Chunker v2 + eval văn bản dài](./phase-02-chunker-v2.md) | Completed |
| 3 | [Đóng gói Python + CLI cơ bản](./phase-03-package-cli.md) | Completed |
| 4 | [doctor + install + script cài](./phase-04-doctor-install.md) | Completed |
| 5 | [MCP stdio qua daemon cục bộ](./phase-05-mcp-bridge.md) | Completed |
| 6 | [Kiểm tra cài mới + phát hành v0.1.0](./phase-06-fresh-install-release.md) | In Progress |

Phụ thuộc: 1 → 2, 3; 3 → 4, 5; 2, 4, 5 → 6.

## Success Criteria
- [ ] Fork `abm-dungtq/tqd-voice` tồn tại, nhánh `tqd/v0.1` đã push, tag `v0.1.0`.
- [ ] Không còn đường dẫn `D:/TTS` cứng trong code runtime; production 8001 vẫn chạy với `D:\TTS\start-api.cmd`.
- [ ] `eval_longform.py compare` in ra quyết định `ADOPT_V2` hoặc `KEEP_LEGACY`; default của `TQD_CHUNKER` khớp quyết định.
- [ ] Cài mới vào `TQD_VOICE_HOME` trống: `doctor`, `install`, `say`, `clone`, `voices` đều exit 0.
- [ ] Script `scripts/smoke/mcp_smoke.py` gọi được `list_voices` + `synthesize_speech` qua stdio, nhận file wav.
- [ ] Bài ~2.700 từ → một file âm thanh liền mạch; ghi lại thời gian chạy trên RTX 3060.
- [ ] `pytest tests/addons tests/tts_service tests/tqd -q`: chỉ còn lỗi đã biết `test_generation_ranges_match_public_api_limits`.

## Dependencies
- `gh` đăng nhập `abm-dungtq` (đã kiểm tra, scope `repo`, `workflow`).
- `uv` 0.13.7 hỗ trợ `uv tool install --torch-backend` (đã kiểm tra).
- Dữ liệu eval do lead soạn: `assets/longform_texts.json` (6 văn bản, 4 giọng).
- Push/fork/tag là hành động bên ngoài: cook phải xin xác nhận người dùng trước khi chạy.

## Validation Log

### Session 1 — 2026-11-25
**Trigger:** `/ak-plan validate` sau khi tạo plan.

### Verification Results
- **Tier:** Full (6 phase; Fact Checker, Flow Tracer, Scope Auditor, Contract Verifier)
- **Claims checked:** 38
- **Verified:** 34 | **Failed:** 2 | **Unverified:** 2

#### Failures
1. [Contract Verifier] Task 1.4 chuyển `studio_voices_meta.json` vào `vizipvoice_addons/data/`, nhưng `scripts/data/guess_region.py:210` có mặc định `--studio-meta studio_voices_meta.json` (đường dẫn tương đối) → mặc định này sẽ hỏng. Phía đọc trong runtime: chỉ `vizipvoice_openai_server.py:139`.
2. [Fact Checker] Task 3.2 pin `torchcodec` "theo freeze", nhưng `torchcodec` không có trong `.venv` (đã kiểm `importlib.metadata`). Các gói `psutil` cũng chưa có (plan đã có bước cài).

#### Unverified
1. [Flow Tracer] Tên chính xác của API client stdio trong mcp 2.3.0: có `mcp/client/stdio.py`, `mcp/client/session.py`; tên hàm/lớp để executor xác minh lúc chạy (Task 5.5 đã ghi rõ).
2. [Scope Auditor] Production cổng 8001 chạy thẳng từ working tree `D:\TTS\ViZipvoice` (`start-api.cmd`: `cd /d "%ROOT%ViZipvoice"` rồi chạy `vizipvoice_openai_server.py`). Plan sửa code và đổi nhánh ngay trong thư mục này → server đang chạy không bị ảnh hưởng (code đã nạp), nhưng lần khởi động lại kế tiếp sẽ chạy code đang làm dở, và có thể đổi mặc định chunker.

#### Verified (chọn lọc)
- `vizipvoice_openai_server.py`: `ROOT`/`HF_HOME` dòng 45–46; `MODEL_DIR`/`META_JSON_PATH`/`LIBRARY_DIR`/`CUSTOM_VOICES_DIR` 137–141; `LIBRARY_DIR` dùng ở 618, 634–635, 711; `split_text_into_sentences` tại 422; `parse_expressive_script` gọi duy nhất ở 1045; `device="cuda", use_fp16=True` 538–539; autocast 958; `num_step` 930/1026; `/health` 1184–1197; `main()` đọc `PORT_HOST`/`PORT` 1489–1492; Form fields `create_voice` 1234–1243 (`file, name, denoise, description, ref_text, prompt_text, gender, region, auto_transcribe, force`).
- `zipvoice/vizipvoice.py:480` autocast cứng cuda; `get_vocoder` → `charactr/vocos-mel-24khz` (`zipvoice/bin/infer_zipvoice.py:311`).
- `vizipvoice_addons/asr.py:53` đường dẫn CT2 cứng; backend mặc định `transformers`.
- `scripts/eval/eval_tts.py`: `_canon` 47, `norm` 164, CER 175–176, resample 16 kHz 130–174.
- mcp 2.3.0, `MCPServer`, `mcp_server.main()` dùng `mcp.run(transport="stdio")`, log ra stderr.
- HF `contextboxai/ViZipvoice` Apache-2.0, có `model.json`, 30 cặp `audio/*.mp3|txt`.
- Header bản quyền `zipvoice/models/zipvoice.py`: "Copyright 2024 Xiaomi Corp.".
- Thư mục `D:\TTS\voice-share-vivos-17` tồn tại (production dùng library voices).

**Questions asked:** 4

#### Questions & Answers
1. **[Architecture]** Phát triển ở đâu, khi production 8001 chạy thẳng từ `D:\TTS\ViZipvoice`?
   - Options: Worktree riêng `D:\TTS\tqd-voice`, production giữ commit nền (Recommended) | Sửa thẳng trong `D:\TTS\ViZipvoice`
   - **Answer:** Worktree riêng (theo đề xuất)
   - **Rationale:** tránh production chạy code dở khi máy khởi động lại.
2. **[Scope]** Nếu chunker v2 thắng, production 8001 có dùng ngay không?
   - Options: Ghim `TQD_CHUNKER=legacy` trong `start-api.cmd`, nghe thử rồi mới đổi (Recommended) | Tự dùng ở lần khởi động lại
   - **Answer:** Ghim legacy (theo đề xuất)
   - **Rationale:** thay đổi giọng đọc production phải qua người nghe.
3. **[Assumptions]** `torchcodec` không có trong venv.
   - Options: Bỏ khỏi dependencies (Recommended) | Thêm vào rồi kiểm tra
   - **Answer:** Bỏ (theo đề xuất)
   - **Rationale:** server đang chạy không cần nó; thêm vào là thêm rủi ro tương thích torch.
4. **[Scope]** Mặc định `--studio-meta` của `guess_region.py` sẽ hỏng khi chuyển file.
   - Options: Sửa mặc định trong Phase 1 (Recommended) | Để nguyên
   - **Answer:** Sửa (theo đề xuất)
   - **Rationale:** giữ script nội bộ chạy được.

#### Confirmed Decisions
- Nơi phát triển: worktree `D:\TTS\tqd-voice` nhánh `tqd/v0.1`; `D:\TTS\ViZipvoice` ở nhánh `tqd/baseline` cho production.
- Production chunker: ghim `legacy` đến khi người dùng quyết định.
- Dependencies: không có `torchcodec`.
- `guess_region.py`: mặc định trỏ `vizipvoice_addons/data/studio_voices_meta.json`.

#### Impact on Phases
- Phase 1: viết lại Task 1.2–1.9 (commit nền, worktree, `start-api.cmd` thêm `TQD_CHUNKER=legacy`, sửa `guess_region.py`).
- Phase 2–6: cwd mặc định là worktree; `$PY` = `D:/TTS/ViZipvoice/.venv/Scripts/python.exe`.
- Phase 2: Task 2.6 ghi rõ production không bị ảnh hưởng.
- Phase 3: bỏ `torchcodec`.
- Phase 6: cài thử từ `D:/TTS/tqd-voice`; tham chiếu quét bí mật trỏ Task 1.3.

### Whole-Plan Consistency Sweep
- Files reread: plan.md, phase-01-start.md, phase-02-chunker-v2.md, phase-03-package-cli.md, phase-04-doctor-install.md, phase-05-mcp-bridge.md, phase-06-fresh-install-release.md
- Decision deltas checked: 4
- Reconciled stale references: 17 (đường dẫn `.venv` tương đối → `$PY`/exe tuyệt đối ở phase 2–6; `uv tool install` trỏ worktree; tham chiếu "Task 1.7 bước 2" → "Task 1.3 bước 2"; `python` trần ở Task 6.2/6.3 → `$PY`; `torchcodec`)
- Unresolved contradictions: 0
- `ak plan validate`: OK; 39 việc, 6 phase, mỗi phase có Failure Protocol.
