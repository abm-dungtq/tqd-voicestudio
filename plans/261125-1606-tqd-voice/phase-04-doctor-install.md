---
phase: 4
title: "doctor + install + script cài"
status: pending
priority: P1
effort: "4h"
dependencies: [3]
---

> Cwd mặc định: `D:\TTS\tqd-voice` (worktree). `$PY` = `D:/TTS/ViZipvoice/.venv/Scripts/python.exe`. Không sửa file trong `D:\TTS\ViZipvoice`.

# Phase 4: doctor + install + script cài

## Goal
`tqd-voice doctor` phân tích máy (OS, GPU, VRAM, driver, RAM, ổ đĩa, ffmpeg, cổng) và đưa ra cấu hình + bước sửa cụ thể; `tqd-voice install` tải model, ghi `config.toml` theo khuyến nghị và chạy thử một câu; `install.ps1` / `install.sh` cho bạn bè cài bằng một lệnh.

## Context (lead đã xác minh)
- `uv tool install` hỗ trợ `--torch-backend=auto` (uv 0.13.7, `uv tool install --help`): uv dò driver CUDA/AMD/Intel để chọn bản PyTorch; không có GPU thì dùng bản CPU. Vì vậy việc chọn bản torch nằm ở lệnh cài, còn `doctor` chỉ phát hiện sai lệch sau khi cài.
- Model `contextboxai/ViZipvoice` Apache-2.0, revision `7903dcb99d5a17c2e59a0ef5f7db32de4bed357b`, có `checkpoint-1860000.pt`, `config.json`, `model.json`, `tokens.txt`, `audio/*.mp3` + `audio/*.txt` (30 giọng studio). Server đọc `MODEL_DIR/audio/*.txt` và dùng `.wav`, không có thì `.mp3` → bản mp3 trên HF là đủ.
- Vocoder tải qua `get_vocoder(None)` từ HF `charactr/vocos-mel-24khz`; ASR mặc định `vinai/PhoWhisper-small` (backend transformers).
- `audio_formats.py` gọi ffmpeg cho mp3/opus/flac; wav không cần ffmpeg.
- Không đổi `num_step` theo phần cứng (chưa có số liệu chất lượng); chỉ đổi device, fp16, `max_streams`.

## Files to Create / Modify
- Create: `tqd_voice/doctor.py`, `tqd_voice/install.py`, `install.ps1`, `install.sh`, `tests/tqd/test_doctor.py`
- Modify: `tqd_voice/cli.py` (thêm lệnh `doctor`, `install`)

## Tasks

### Task 4.1 — Thu thập thông tin máy
- Target: `tqd_voice/doctor.py`.
- Steps: viết
  ```python
  @dataclass
  class Facts:
      os: str; arch: str; python: str
      torch_version: str; torch_cuda: str | None; cuda_available: bool
      gpu_name: str | None; vram_total_gb: float | None
      nvidia_smi: bool; driver_version: str | None
      ram_total_gb: float; ram_avail_gb: float; disk_free_gb: float
      ffmpeg: str | None; port: int; port_state: str   # "free" | "tqd" | "other"
      model_present: bool

  def collect(cfg: Config) -> Facts
  ```
  Nguồn: `platform.system()/machine()/python_version()`; `torch.__version__`, `torch.version.cuda`, `torch.cuda.is_available()`, `torch.cuda.get_device_name(0)`, `torch.cuda.get_device_properties(0).total_memory`; `nvidia-smi --query-gpu=driver_version --format=csv,noheader` qua `shutil.which` + `subprocess.run(timeout=10)`; `psutil.virtual_memory()`; `shutil.disk_usage(paths.home() nếu tồn tại, ngược lại thư mục cha gần nhất tồn tại)`; `shutil.which("ffmpeg")`; `port_state` dùng `daemon.health` (dict có `voices` → `"tqd"`), nếu không có thì thử `socket.bind(("127.0.0.1", port))` (bind được → `"free"`, lỗi → `"other"`); `model_present` = `(paths.model_dir()/"checkpoint-1860000.pt").is_file()`.
- Verify: phủ bởi Task 4.5.

### Task 4.2 — Luật khuyến nghị (hàm thuần)
- Target: `tqd_voice/doctor.py`.
- Steps: viết `recommend(f: Facts, base: Config) -> tuple[Config, list[Check]]` với `Check(level: "ok"|"warn"|"error", message: str, fix: str = "")`; áp đúng thứ tự:
  1. `cuda_available` và `vram >= 6` → `device="cuda", fp16=True, max_streams = 4 if vram >= 10 else 2`; ok `"GPU {name} {vram:.0f} GB"`.
  2. `cuda_available` và `4 <= vram < 6` → `cuda, fp16=True, max_streams=1`; warn `"VRAM thấp, chỉ chạy 1 luồng"`.
  3. `cuda_available` và `vram < 4` → `device="cpu", fp16=False, max_streams=1`; warn `"VRAM dưới 4 GB, chuyển sang CPU"`.
  4. không `cuda_available`, có `nvidia_smi`, `torch_cuda is None` → `cpu, fp16=False, max_streams=1`; error `"Máy có GPU NVIDIA (driver {driver}) nhưng PyTorch là bản CPU"`, fix `"uv tool install --reinstall --python 3.12 --torch-backend=auto git+https://github.com/abm-dungtq/tqd-voice@v0.1.0"`.
  5. không `cuda_available`, có `nvidia_smi`, `torch_cuda` khác None → `cpu`; error `"PyTorch CUDA {torch_cuda} không chạy được với driver {driver}"`, fix `"Cập nhật driver NVIDIA mới nhất, hoặc cài lại với --torch-backend=auto"`.
  6. không GPU → `cpu, fp16=False, max_streams=1`; warn `"Không có GPU NVIDIA: chạy CPU, chậm. Đo tốc độ bằng: tqd-voice doctor --bench"`.
  7. `disk_free_gb < 3` → error `"Ổ đĩa còn {x:.1f} GB, cần ít nhất 3 GB"`.
  8. `ram_total_gb < 8` → warn `"RAM {x:.0f} GB, khuyến nghị ≥ 8 GB"`.
  9. `ffmpeg is None` → warn `"Thiếu ffmpeg: chỉ xuất được wav"`, fix theo OS: Windows `"winget install --id Gyan.FFmpeg -e"`, Linux `"sudo apt install ffmpeg"`, khác `"Cài ffmpeg và thêm vào PATH"`.
  10. `port_state == "other"` → warn `"Cổng {port} đang bị chương trình khác dùng"`, fix `"Sửa port trong {paths.config_path()}"`.
  11. `not model_present` → warn `"Chưa có model"`, fix `"tqd-voice install"`.
  Các trường còn lại của Config giữ từ `base`.
- Verify: phủ bởi Task 4.5.

### Task 4.3 — Lệnh `doctor`
- Target: `tqd_voice/cli.py`, `tqd_voice/doctor.py`.
- Steps: `tqd-voice doctor [--json] [--bench]`:
  - Mặc định in 3 khối: "Máy" (các trường Facts), "Kiểm tra" (mỗi Check một dòng `[OK]/[CẢNH BÁO]/[LỖI] message` + dòng `  → fix` nếu có), "Cấu hình đề xuất" (device, fp16, max_streams).
  - `--json`: in `{"facts": asdict(f), "checks": [...], "recommended": asdict(cfg)}`.
  - `--bench`: `ensure_server` với cấu hình hiện tại, POST `/v1/audio/speech` câu cố định `"Đọc sách là một thói quen tốt, nhưng ngày càng ít người duy trì được trong nhịp sống hiện đại, khi điện thoại chiếm gần hết thời gian rảnh."` dạng wav 24000 Hz, đo thời gian; in `Thời gian: X s | Âm thanh: Y s | Tốc độ: Y/X lần thời gian thực`.
  - Exit code 1 nếu có Check `error`, ngược lại 0.
- Verify: `PYTHONUTF8=1 D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe doctor --json` exit 0; JSON có `facts.cuda_available == true`, `facts.gpu_name` chứa `3060`, `recommended.device == "cuda"`, `recommended.max_streams == 4`.

### Task 4.4 — Lệnh `install`
- Target: `tqd_voice/install.py`, `tqd_voice/cli.py`.
- Steps: `tqd-voice install [--no-asr] [--no-smoke]`:
  1. `collect` + `recommend` với `config.load()`; in như doctor. Có Check `error` → in fix, return 1 (không tải gì).
  2. Tạo `paths.home()`, `voices_dir()`, `outputs_dir()`, `logs_dir()`, `hf_cache()`; đặt `os.environ["HF_HOME"] = str(paths.hf_cache())` trước khi import `huggingface_hub`.
  3. `snapshot_download("contextboxai/ViZipvoice", revision="7903dcb99d5a17c2e59a0ef5f7db32de4bed357b", local_dir=paths.model_dir(), allow_patterns=["checkpoint-1860000.pt", "config.json", "model.json", "tokens.txt", "audio/*"])`.
  4. `snapshot_download("charactr/vocos-mel-24khz")` (vào HF cache).
  5. Nếu không `--no-asr`: `snapshot_download("vinai/PhoWhisper-small")`.
  6. `config.save(cfg_đề_xuất)`; in đường dẫn.
  7. Nếu không `--no-smoke`: `ensure_server`; POST câu `"Xin chào, TQD Voice đã cài đặt xong."` → `paths.outputs_dir()/"hello.wav"`; `stop_server`; in đường dẫn file.
  8. In "Xong. Thử: tqd-voice say \"Xin chào\"" và đoạn cấu hình MCP (phase 5 bổ sung nội dung).
- Verify: với `PYTHONUTF8=1 TQD_VOICE_HOME=D:/TTS/tqd-install-home` (thư mục trống, không đặt `VIZIPVOICE_MODEL_DIR`/`HF_HOME`): `D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe install` exit 0; tồn tại `D:/TTS/tqd-install-home/models/ViZipvoice/checkpoint-1860000.pt`, `D:/TTS/tqd-install-home/config.toml` (chứa `device = "cuda"`), `D:/TTS/tqd-install-home/outputs/hello.wav` (> 10000 byte); `ls D:/TTS/tqd-install-home/models/ViZipvoice/audio/*.txt | wc -l` in `30`.

### Task 4.5 — Test luật khuyến nghị
- Target: `tests/tqd/test_doctor.py`.
- Steps: tạo `Facts` giả bằng helper với giá trị mặc định "máy tốt" (Windows, cuda, 12 GB, disk 100, ram 32, ffmpeg có, port free, model có) rồi test:
  1. 12 GB → `device=="cuda"`, `max_streams==4`, không có `error`.
  2. 8 GB → `max_streams==2`.
  3. 5 GB → `max_streams==1`, có `warn`.
  4. 3 GB → `device=="cpu"`.
  5. `cuda_available=False, nvidia_smi=True, torch_cuda=None` → có `error`, `fix` chứa `--torch-backend=auto`.
  6. Không GPU (`cuda_available=False, nvidia_smi=False`) → `device=="cpu"`, `fp16 is False`, có `warn`.
  7. `disk_free_gb=2` → có `error`.
  8. `ffmpeg=None, os="Windows"` → `fix` chứa `winget`; `os="Linux"` → chứa `apt`.
- Verify: `PYTHONUTF8=1 $PY -m pytest tests/tqd/test_doctor.py -q` exit 0.

### Task 4.6 — Script cài một lệnh
- Target: `install.ps1`, `install.sh` (gốc repo).
- Steps:
  - `install.ps1` tham số `[string]$Ref = "v0.1.0"`, `[switch]$DryRun`. Nếu `Get-Command uv` không có → in `Chưa có uv. Cài bằng: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` rồi `exit 1`. Ba lệnh: `uv tool install --python 3.12 --torch-backend=auto "git+https://github.com/abm-dungtq/tqd-voice@$Ref"`, `tqd-voice doctor`, `tqd-voice install`. `-DryRun` chỉ in ba lệnh. Dừng ngay khi một lệnh trả mã khác 0 (trừ `doctor`: in cảnh báo rồi vẫn chạy `install`, vì `install` tự chặn khi có lỗi).
  - `install.sh` tương đương (`set -euo pipefail`, `REF="${TQD_REF:-v0.1.0}"`, cờ `--dry-run`, gợi ý uv `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- Verify: `powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -DryRun` exit 0 và in đúng 3 dòng lệnh có `--torch-backend=auto`; `bash -n install.sh` exit 0; `bash install.sh --dry-run` exit 0 và in 3 dòng lệnh.
- Commit: "Add doctor, install and one-line installers".

## Todo
- [ ] Task 4.1 — Thu thập thông tin máy
- [ ] Task 4.2 — Luật khuyến nghị (hàm thuần)
- [ ] Task 4.3 — Lệnh `doctor`
- [ ] Task 4.4 — Lệnh `install`
- [ ] Task 4.5 — Test luật khuyến nghị
- [ ] Task 4.6 — Script cài một lệnh

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
