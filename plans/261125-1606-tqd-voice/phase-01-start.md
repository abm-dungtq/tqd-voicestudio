---
phase: 1
title: "Repo setup: fork, hygiene, đường dẫn cấu hình"
status: completed
priority: P1
effort: "2h"
dependencies: []
---

# Phase 1: Repo setup

## Goal
Có fork `abm-dungtq/tqd-voicestudio`; công việc hiện tại được chốt thành commit nền trên nhánh `tqd/baseline` ngay trong `D:\TTS\ViZipvoice` (thư mục production, không đổi nội dung file nào); mọi phát triển tiếp diễn trong worktree riêng `D:\TTS\tqd-voice` nhánh `tqd/v0.1`; mọi đường dẫn runtime đọc từ `TQD_VOICE_HOME` thay vì `D:/TTS` cứng, còn production cổng 8001 vẫn chạy code nền với đường dẫn cũ.

## Context
- Repo local: `D:\TTS\ViZipvoice`, remote `origin` = `iamdinhthuan/ViZipvoice`, nhánh `master`, nhiều file chưa commit (xem `git status`).
- Server: `vizipvoice_openai_server.py` dòng 45–46 (`ROOT`, `HF_HOME`), 137–141 (`MODEL_DIR`, `META_JSON_PATH`, `LIBRARY_DIR`, `CUSTOM_VOICES_DIR`).
- ASR: `vizipvoice_addons/asr.py` dòng ~53 mặc định `D:/TTS/models/phowhisper-small-ct2`.
- Production khởi động bằng `D:\TTS\start-api.cmd` (ngoài repo): `cd /d "%ROOT%ViZipvoice"` rồi chạy `.venv\Scripts\python.exe vizipvoice_openai_server.py` → production đọc thẳng working tree `D:\TTS\ViZipvoice`. Vì vậy KHÔNG sửa code trong thư mục này sau Task 1.3; mọi thay đổi code làm trong `D:\TTS\tqd-voice`.
- Worktree dùng chung venv: `D:/TTS/ViZipvoice/.venv/Scripts/python.exe` (gọi tắt `$PY` trong mọi phase).
<!-- Updated: Validation Session 1 - develop in separate worktree; production pinned to baseline commit -->

## Files to Create / Modify
- Create: `tqd_voice/__init__.py`, `tqd_voice/paths.py`, `vizipvoice_addons/data/studio_voices_meta.json` (move từ root), `tests/tqd/test_paths.py`
- Modify: `.gitignore`, `vizipvoice_openai_server.py`, `vizipvoice_addons/asr.py`, `scripts/data/guess_region.py`, `D:\TTS\start-api.cmd`
- Mọi đường dẫn repo từ Task 1.5 trở đi nằm trong `D:\TTS\tqd-voice`.

## Tasks

### Task 1.1 — Tạo fork trên GitHub (hành động bên ngoài, cần người dùng xác nhận trước)
- Goal: repo `abm-dungtq/tqd-voicestudio` tồn tại và là fork của `iamdinhthuan/ViZipvoice`.
- Target: GitHub.
- Steps:
  1. `gh repo fork iamdinhthuan/ViZipvoice --clone=false --fork-name tqd-voice`
- Success criteria: repo hiện trên tài khoản `abm-dungtq`.
- Verify: `gh repo view abm-dungtq/tqd-voicestudio --json name,isFork,parent -q '.name+" "+(.isFork|tostring)+" "+.parent.name'` in ra `tqd-voice true ViZipvoice`.

### Task 1.2 — Dọn .gitignore (trong `D:\TTS\ViZipvoice`)
- Goal: artifact lớn và dữ liệu không bị commit.
- Target: `D:\TTS\ViZipvoice\.gitignore`.
- Steps: thêm các dòng (nếu chưa có): `egs/zipvoice/data_smoke_*/`, `egs/zipvoice/exp/`, `egs/zipvoice/download/`, `egs/zipvoice/data/`, `__pycache__/`, `*.pyc`, `.venv/`, `plans/**/research/`.
- Success criteria: `git status --short` không còn liệt kê `egs/zipvoice/data_smoke_vnavc_1795535568/`.
- Verify: `git -C D:/TTS/ViZipvoice status --short | grep -c data_smoke` in ra `0`.

### Task 1.3 — Commit nền trong thư mục production (không đổi nội dung file)
- Goal: toàn bộ công việc hiện tại nằm trong nhánh `tqd/baseline`; thư mục production giữ nguyên nội dung.
- Steps (cwd `D:\TTS\ViZipvoice`):
  1. `git checkout -b tqd/baseline` (giữ nguyên working tree).
  2. Quét bí mật: `grep -rIn "hf_[A-Za-z0-9]\{20,\}\|sk-[A-Za-z0-9]\{20,\}" --exclude-dir=.venv --exclude-dir=.git --exclude-dir=egs . | wc -l` phải in `0`.
  3. `git add -A`; commit message "Baseline: addons, data toolkit, finetune recipe, eval, TQD-Voice plan" kèm trailer Devin theo quy ước repo.
- Success criteria: working tree sạch, không file nào đổi nội dung.
- Verify: `git -C D:/TTS/ViZipvoice status --short | wc -l` in `0`; `curl -s http://127.0.0.1:8001/health` vẫn có `"status":"ok"`.

### Task 1.4 — Tạo worktree phát triển
- Steps: `git -C D:/TTS/ViZipvoice worktree add D:/TTS/tqd-voice -b tqd/v0.1 tqd/baseline`.
- Verify: `git -C D:/TTS/tqd-voice branch --show-current` in `tqd/v0.1`; `test -f D:/TTS/tqd-voice/plans/261125-1606-tqd-voice/assets/longform_texts.json` exit 0.

### Task 1.5 — Module đường dẫn `tqd_voice/paths.py`
- Goal: một nguồn duy nhất cho mọi thư mục dữ liệu.
- Target: `tqd_voice/paths.py`, `tqd_voice/__init__.py` (chứa `__version__ = "0.1.0"`).
- Steps: viết `paths.py` với đúng các hàm sau (không thêm hàm khác):
  ```python
  def home() -> Path:        # Path(os.environ["TQD_VOICE_HOME"]) nếu có, ngược lại Path.home()/".tqd-voice"; .expanduser()
  def model_dir() -> Path:   # env VIZIPVOICE_MODEL_DIR nếu có, ngược lại home()/"models"/"ViZipvoice"
  def voices_dir() -> Path:  # env VIZIPVOICE_VOICES_DIR nếu có, ngược lại home()/"voices"
  def hf_cache() -> Path:    # env HF_HOME nếu có, ngược lại home()/"cache"/"hf"
  def ct2_asr_dir() -> Path: # env VIZIPVOICE_CT2_MODEL nếu có, ngược lại home()/"models"/"phowhisper-small-ct2"
  def outputs_dir() -> Path: # home()/"outputs"
  def logs_dir() -> Path:    # home()/"logs"
  def config_path() -> Path: # home()/"config.toml"
  ```
  Các hàm không tạo thư mục (chỉ trả về đường dẫn).
- Verify: phủ bởi Task 1.8.

### Task 1.6 — Server, ASR và script dùng đường dẫn mới
- Target: `vizipvoice_openai_server.py` (khối hằng dòng 45–46, 137–141; `LIBRARY_DIR` ở 618, 634–635, 711), `vizipvoice_addons/asr.py` (dòng 34, 53), `scripts/data/guess_region.py` (dòng 13, 210).
- Steps:
  1. Xóa `ROOT = Path(__file__).resolve().parent.parent`. Thay `os.environ.setdefault("HF_HOME", str(ROOT / ".hf-cache"))` bằng `os.environ.setdefault("HF_HOME", str(paths.hf_cache()))`; `from tqd_voice import paths` phải đứng TRƯỚC `from zipvoice.vizipvoice import ...` (dòng 49; module đó import `huggingface_hub`).
  2. `MODEL_DIR = paths.model_dir()`; `CUSTOM_VOICES_DIR = paths.voices_dir()`.
  3. `LIBRARY_DIR = Path(os.environ["VIZIPVOICE_LIBRARY_DIR"]) if os.environ.get("VIZIPVOICE_LIBRARY_DIR") else None`; ở dòng 618 và 711 chỉ dùng `LIBRARY_DIR` khi khác `None`; dòng 634 đổi thành `if LIBRARY_DIR is not None and LIBRARY_DIR.is_dir():`.
  4. `mkdir -p vizipvoice_addons/data` rồi `git mv studio_voices_meta.json vizipvoice_addons/data/studio_voices_meta.json`; `META_JSON_PATH = Path(vizipvoice_addons.__file__).resolve().parent / "data" / "studio_voices_meta.json"` (thêm `import vizipvoice_addons`).
  5. `asr.py`: thay `"D:/TTS/models/phowhisper-small-ct2"` bằng `str(paths.ct2_asr_dir())`; sửa docstring dòng 34 thành `<TQD_VOICE_HOME>/models/phowhisper-small-ct2`.
  6. `guess_region.py`: mặc định `--studio-meta` (dòng 210) và ví dụ ở docstring dòng 13 → `vizipvoice_addons/data/studio_voices_meta.json`.
  <!-- Updated: Validation Session 1 - guess_region default path follows the moved meta file -->
- Verify: `grep -rn "D:/TTS" vizipvoice_openai_server.py mcp_server.py vizipvoice_addons zipvoice tqd_voice --include=*.py | wc -l` in `0`; `grep -n "studio_voices_meta" scripts/data/guess_region.py` chỉ chứa đường dẫn mới.

### Task 1.7 — Giữ production trên máy này
- Goal: `D:\TTS\start-api.cmd` trỏ đúng dữ liệu cũ khi sau này production chạy code mới, và ghim chunker cũ.
- Target: `D:\TTS\start-api.cmd`.
- Steps: thêm ngay trước dòng `cd /d`: `set "VIZIPVOICE_MODEL_DIR=D:\TTS\models\ViZipvoice"`, `set "VIZIPVOICE_VOICES_DIR=D:\TTS\voices"`, `set "VIZIPVOICE_CT2_MODEL=D:\TTS\models\phowhisper-small-ct2"`, `set "VIZIPVOICE_LIBRARY_DIR=D:\TTS\voice-share-vivos-17\voice-share-vivos-17\library"`, `set "TQD_CHUNKER=legacy"`. Giữ nguyên `HF_HOME` và dòng chạy. KHÔNG khởi động lại server 8001 (code nền không đọc các biến mới nên vô hại).
  <!-- Updated: Validation Session 1 - production pins TQD_CHUNKER=legacy until the user listens -->
- Verify: từ `D:\TTS\tqd-voice`, chạy server dev `PORT=8011` với đúng 5 biến trên + `HF_HOME=D:/TTS/.hf-cache PYTHONUTF8=1` bằng `$PY vizipvoice_openai_server.py`; khi sẵn sàng, trường `voices` của `curl -s http://127.0.0.1:8011/health` bằng của `curl -s http://127.0.0.1:8001/health`. Tắt server 8011.

### Task 1.8 — Test đường dẫn
- Target: `tests/tqd/test_paths.py` (+ `tests/tqd/__init__.py` rỗng nếu cần).
- Steps: test (dùng `monkeypatch`): (a) `TQD_VOICE_HOME=tmp` → `model_dir()==tmp/models/ViZipvoice`, `voices_dir()==tmp/voices`; (b) có `VIZIPVOICE_MODEL_DIR` → ưu tiên env; (c) không có env → `home()==Path.home()/".tqd-voice"`.
- Verify: (cwd `D:\TTS\tqd-voice`) `PYTHONUTF8=1 $PY -m pytest tests/tqd tests/addons tests/tts_service -q` — chỉ fail đúng `test_generation_ranges_match_public_api_limits`.

### Task 1.9 — Commit và đẩy nhánh phát triển (push cần người dùng xác nhận)
- Steps (cwd `D:\TTS\tqd-voice`): quét bí mật như Task 1.3 bước 2; `git add -A`; commit "Configurable data paths for TQD-Voice"; `git remote add tqd https://github.com/abm-dungtq/tqd-voicestudio.git` (remote dùng chung giữa các worktree; bỏ qua nếu đã có); `git push -u tqd tqd/v0.1`.
- Verify: `git ls-remote tqd tqd/v0.1 | wc -l` in `1`.

## Todo
- [ ] Task 1.1 — Tạo fork trên GitHub (hành động bên ngoài, cần người dùng xác nhận trước)
- [x] Task 1.2 — Dọn .gitignore (trong `D:\TTS\ViZipvoice`)
- [x] Task 1.3 — Commit nền trong thư mục production (không đổi nội dung file)
- [x] Task 1.4 — Tạo worktree phát triển
- [x] Task 1.5 — Module đường dẫn `tqd_voice/paths.py`
- [x] Task 1.6 — Server, ASR và script dùng đường dẫn mới
- [x] Task 1.7 — Giữ production trên máy này
- [x] Task 1.8 — Test đường dẫn
- [ ] Task 1.9 — Commit và đẩy nhánh phát triển (push cần người dùng xác nhận)

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
