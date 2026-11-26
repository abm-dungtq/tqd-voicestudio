---
phase: 3
title: "Đóng gói Python + CLI cơ bản"
status: completed
priority: P1
effort: "4h"
dependencies: [1]
---

> Cwd mặc định: `D:\TTS\tqd-voice` (worktree). `$PY` = `D:/TTS/ViZipvoice/.venv/Scripts/python.exe`. Không sửa file trong `D:\TTS\ViZipvoice`.

# Phase 3: Đóng gói Python + CLI cơ bản

## Goal
Repo cài được bằng `uv tool install`, cung cấp lệnh `tqd-voice` với `serve`, `say`, `clone`, `voices`, `stop`; server chạy được trên cả CUDA và CPU; CLI tự bật server nền ở `127.0.0.1:8765` khi cần.

## Context (lead đã xác minh)
- `pyproject.toml` hiện chỉ có cấu hình isort/black/pytest, chưa có `[project]`.
- Server cứng `device="cuda", use_fp16=True` (`vizipvoice_openai_server.py:538-539`), autocast cứng `device_type="cuda"` (dòng ~958), `num_step: int = 16` mặc định ở dòng ~930 và ~1026.
- `/health` trả 503 `{"status":"loading"}` khi đang nạp, 200 `{"status":"ok", "voices": N, ...}` khi sẵn sàng.
- `zipvoice/tokenizer/tokenizer.py` import `piper_phonemize` trong `try` (không bắt buộc); `jieba`, `pypinyin`, `cn2an`, `inflect`, `lhotse` là import cứng → phải nằm trong dependencies.
- ASR mặc định backend `transformers` (`VIZIPVOICE_ASR_BACKEND`), model `vinai/PhoWhisper-small`.
- Cổng 8000/8001 đã dùng trên máy này → mặc định của tool là 8765.

## Files to Create / Modify
- Create: `tqd_voice/config.py`, `tqd_voice/daemon.py`, `tqd_voice/cli.py`, `tqd_voice/__main__.py`, `tests/tqd/test_config.py`, `tests/tqd/test_cli.py`
- Modify: `pyproject.toml`, `vizipvoice_openai_server.py`

## Tasks

### Task 3.1 — Device / fp16 / num_step cấu hình được
- Target: `vizipvoice_openai_server.py`.
- Steps:
  1. Thêm hằng gần `MAX_STREAMS`:
     ```python
     _dev = os.environ.get("VIZIPVOICE_DEVICE", "auto").strip().lower()
     DEVICE = ("cuda" if torch.cuda.is_available() else "cpu") if _dev == "auto" else _dev
     USE_FP16 = DEVICE == "cuda" and os.environ.get("VIZIPVOICE_FP16", "1").strip() not in ("0", "false", "no")
     NUM_STEP = int(os.environ.get("VIZIPVOICE_NUM_STEP", "16"))
     ```
  2. Dòng 538–539: `device=DEVICE, use_fp16=USE_FP16`.
  3. Autocast (dòng ~958): `torch.autocast(device_type="cuda" if DEVICE == "cuda" else "cpu", dtype=torch.float16, enabled=self.tts.use_fp16)`.
  4. Hai chữ ký `num_step: int = 16` (dòng ~930, ~1026) → `num_step: int = NUM_STEP`.
  5. `/health`: `"backend": f"vizipvoice-{DEVICE}-{'fp16' if USE_FP16 else 'fp32'}"`.
  6. Grep thêm `"cuda"` trong file; mọi chỗ cứng còn lại đổi sang `DEVICE`.
  7. `zipvoice/vizipvoice.py` dòng ~480: `torch.autocast(device_type="cuda", ...)` → `device_type=self.device.type` và `enabled=self.use_fp16 and self.device.type == "cuda"`. Grep `"cuda"` trong `zipvoice/vizipvoice.py` và `zipvoice/utils/infer.py`; chỉ giữ chỗ nằm trong `_resolve_device`.
- Verify: khởi động server với `VIZIPVOICE_DEVICE=cpu PORT=8012 VIZIPVOICE_MODEL_DIR=D:/TTS/models/ViZipvoice VIZIPVOICE_VOICES_DIR=D:/TTS/voices HF_HOME=D:/TTS/.hf-cache PYTHONUTF8=1`; khi `/health` 200, `curl -s http://127.0.0.1:8012/health` chứa `vizipvoice-cpu-fp32`; `curl -s -X POST http://127.0.0.1:8012/v1/audio/speech -H "Content-Type: application/json" -d '{"input":"Xin chào.","voice":"Đinh-Quyết","response_format":"wav"}' -o D:/TTS/tmp/cpu.wav` và file > 10000 byte. Tắt server.

### Task 3.2 — `pyproject.toml` có `[project]`
- Steps:
  1. Chạy `uv pip freeze --python $PY` để lấy phiên bản đang chạy được.
  2. Thêm (giữ nguyên các khối `[tool.isort]`, `[tool.black]`, `[tool.pytest.ini_options]`):
     ```toml
     [build-system]
     requires = ["setuptools>=69,<81", "wheel"]
     build-backend = "setuptools.build_meta"

     [project]
     name = "tqd-voice"
     version = "0.1.0"
     description = "TQD-Voice: Vietnamese TTS + voice cloning CLI and MCP server (ViZipVoice/ZipVoice)"
     readme = "README.md"
     license = { text = "Apache-2.0" }
     requires-python = ">=3.11,<3.13"
     dependencies = [ ... ]

     [project.scripts]
     tqd-voice = "tqd_voice.cli:main"

     [tool.setuptools]
     py-modules = ["vizipvoice_openai_server", "mcp_server"]

     [tool.setuptools.packages.find]
     include = ["zipvoice*", "vizipvoice_addons*", "tqd_voice*"]

     [tool.setuptools.package-data]
     vizipvoice_addons = ["data/*.json"]
     ```
  3. `dependencies` = đúng danh sách này, pin `==` theo phiên bản trong freeze, trừ dòng ghi chú khác: `torch` (`==2.8.*`), `torchaudio` (`==2.8.*`), `numpy`, `lhotse`, `huggingface_hub`, `safetensors`, `vocos`, `pydub`, `soundfile`, `cn2an`, `inflect`, `soe-vinorm`, `jieba`, `pypinyin`, `setuptools<81` (giữ nguyên ràng buộc), `typing_extensions`, `fastapi`, `uvicorn[standard]`, `python-multipart`, `pydantic`, `httpx`, `mcp` (`>=2.3,<3`), `transformers`, `psutil`. Nếu `psutil` chưa có trong venv thì cài `uv pip install --python $PY psutil` rồi pin theo bản vừa cài. Không thêm `torchcodec` (không có trong venv, server chạy không cần — Validation Session 1), `modal`, `gradio`, `tensorboard`, `piper_phonemize`, `faster-whisper`, `ctranslate2`.
- Verify: `uv build --wheel -o D:/TTS/tmp/dist` exit 0; `python -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('D:/TTS/tmp/dist/tqd_voice-0.1.0-*.whl'))[-1]); n=z.namelist(); print('vizipvoice_addons/data/studio_voices_meta.json' in n, 'vizipvoice_openai_server.py' in n, 'tqd_voice/cli.py' in n, 'zipvoice/models/zipvoice.py' in n, 'zipvoice/tokenizer/tokenizer.py' in n)"` in ra `True True True True True` (chạy sau Task 3.5).

### Task 3.3 — `tqd_voice/config.py`
- Steps: viết:
  ```python
  @dataclass
  class Config:
      port: int = 8765
      device: str = "auto"        # auto | cuda | cpu
      fp16: bool = True
      num_step: int = 16
      max_streams: int = 2
      default_voice: str = "Đinh-Quyết"
      chunker: str = ""           # "" = theo mặc định server; hoặc legacy | v2

  def load() -> Config      # đọc paths.config_path() bằng tomllib nếu tồn tại; khóa lạ bị bỏ qua; thiếu file -> Config()
  def save(cfg: Config) -> Path   # tạo thư mục cha; ghi TOML phẳng: chuỗi trong dấu ", bool true/false, int; UTF-8
  def to_env(cfg: Config) -> dict[str, str]
      # PORT, PORT_HOST="127.0.0.1", VIZIPVOICE_DEVICE, VIZIPVOICE_FP16 ("1"/"0"), VIZIPVOICE_NUM_STEP,
      # VIZIPVOICE_MAX_STREAMS, HF_HOME=paths.hf_cache(), VIZIPVOICE_MODEL_DIR=paths.model_dir(),
      # VIZIPVOICE_VOICES_DIR=paths.voices_dir(), PYTHONUTF8="1"; TQD_CHUNKER chỉ khi cfg.chunker != ""
  ```
- Verify: phủ bởi Task 3.6.

### Task 3.4 — `tqd_voice/daemon.py`
- Steps: viết đúng các hàm:
  - `base_url(cfg) -> str`: `f"http://127.0.0.1:{cfg.port}"`.
  - `health(cfg, timeout=1.0) -> dict | None`: GET `/health`; trả JSON khi HTTP 200, `{"status": "loading"}` khi 503, `None` khi lỗi kết nối.
  - `ensure_server(cfg, wait_s=300) -> str`: nếu `health` có `status == "ok"` → trả `base_url`. Nếu cổng trả 200 nhưng JSON không có khóa `voices` → `RuntimeError("port {port} is used by another program")`. Ngược lại tạo `paths.home()` và `paths.logs_dir()` (`mkdir(parents=True, exist_ok=True)`), rồi spawn `[sys.executable, "-m", "tqd_voice", "serve"]` với `env = os.environ | config.to_env(cfg)`, stdout+stderr ghi nối vào `paths.logs_dir()/"server.log"`, Windows `creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP`, POSIX `start_new_session=True`; ghi PID vào `paths.home()/"server.pid"`; in ra stderr `Đang khởi động TQD-Voice server (lần đầu có thể mất 1–2 phút)...`; poll `health` mỗi 2 s tới `wait_s`; quá hạn → `RuntimeError` kèm 20 dòng cuối `server.log`.
  - `stop_server(cfg) -> bool`: đọc pidfile, kết thúc tiến trình (`psutil.Process(pid).terminate()`, chờ 10 s, rồi `kill`), xóa pidfile; trả `False` nếu không có pidfile hoặc tiến trình không tồn tại.
- Verify: phủ bởi Task 3.7.

### Task 3.5 — `tqd_voice/cli.py` và `tqd_voice/__main__.py`
- Steps:
  1. `__main__.py`: `from tqd_voice.cli import main; raise SystemExit(main())`.
  2. `cli.py` dùng `argparse`, `main(argv=None) -> int`, các lệnh con:
     - `serve [--port N]`: `cfg = config.load()`; ghi đè port nếu có; `os.environ.update(config.to_env(cfg))`; rồi `import vizipvoice_openai_server; vizipvoice_openai_server.main()`.
     - `say [TEXT] [-f FILE] [-v VOICE] [-o OUT] [--format {wav,mp3,flac,opus}] [--speed X]`: văn bản lấy từ TEXT, hoặc `-f` (UTF-8), hoặc stdin nếu TEXT là `-`; thiếu cả hai → lỗi argparse. `ensure_server`; POST `/v1/audio/speech` JSON `{"input": text, "voice": VOICE or cfg.default_voice, "response_format": fmt}` (+ `"speed"` nếu có), `timeout=None`. OUT mặc định `paths.outputs_dir()/f"tqd-{YYYYmmdd-HHMMSS}.{fmt}"`. Ghi file, in đường dẫn tuyệt đối. HTTP khác 200 → in `detail` ra stderr, return 1.
     - `clone NAME AUDIO [--text T] [--gender {male,female}] [--region {bac,trung,nam}] [--force]`: đọc chữ ký `create_voice` trong `vizipvoice_openai_server.py` (dòng ~1231–1300) và dùng ĐÚNG tên trường Form/File ở đó; POST multipart `/v1/voices`; in JSON trả về; khác 2xx → stderr + return 1.
     - `voices [--gender G] [--region R] [--json]`: GET `/v1/voices` với params; mặc định in bảng cột `name`, `gender`, `region`, `kind`; `--json` in JSON gốc.
     - `stop`: gọi `stop_server`, in `stopped` hoặc `not running`, return 0.
- Verify: `PYTHONUTF8=1 $PY -m tqd_voice --help` exit 0 và liệt kê `serve say clone voices stop`.

### Task 3.6 — Unit test
- Target: `tests/tqd/test_config.py`, `tests/tqd/test_cli.py`.
- Steps:
  - `test_config.py`: (a) `TQD_VOICE_HOME=tmp_path`, `save(Config(port=9999, device="cpu", fp16=False, chunker="v2"))` rồi `load()` bằng đúng giá trị; (b) `to_env(Config(device="cpu", fp16=False))["VIZIPVOICE_FP16"] == "0"` và không có khóa `TQD_CHUNKER`; (c) file TOML có khóa lạ vẫn load được.
  - `test_cli.py`: (a) `main(["--help"])` gây `SystemExit(0)`; (b) `daemon.health(Config(port=<cổng trống lấy bằng socket bind 0>))` là `None`; (c) `main(["say"])` (không văn bản) gây `SystemExit(2)`.
- Verify: `PYTHONUTF8=1 $PY -m pytest tests/tqd -q` exit 0.

### Task 3.7 — Chạy thật với venv repo
- Steps:
  1. `uv pip install --python $PY -e . --no-deps`
  2. Biến môi trường: `PYTHONUTF8=1 TQD_VOICE_HOME=D:/TTS/tqd-dev-home VIZIPVOICE_MODEL_DIR=D:/TTS/models/ViZipvoice HF_HOME=D:/TTS/.hf-cache`.
  3. `D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe say "Xin chào các bạn, đây là TQD Voice." -o D:/TTS/tqd-dev-home/hello.wav`
  4. `D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe voices`
  5. `D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe clone testvoice D:/TTS/voices/vi-nam-nu-ngocanh-tunhien-v1.wav --gender female --region nam`
  6. `D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe stop`
- Success criteria / Verify (tất cả phải đúng):
  - bước 3 exit 0 và `D:/TTS/tqd-dev-home/hello.wav` > 10000 byte;
  - bước 4 exit 0 và in ≥ 30 dòng giọng;
  - bước 5 exit 0 và `D:/TTS/tqd-dev-home/voices/testvoice.wav` tồn tại;
  - bước 6 in `stopped`; sau đó `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/health` in `000`.
- Commit: "Package as tqd-voice CLI with local server daemon".

## Todo
- [x] Task 3.1 — Device / fp16 / num_step cấu hình được
- [x] Task 3.2 — `pyproject.toml` có `[project]`
- [x] Task 3.3 — `tqd_voice/config.py`
- [x] Task 3.4 — `tqd_voice/daemon.py`
- [x] Task 3.5 — `tqd_voice/cli.py` và `tqd_voice/__main__.py`
- [x] Task 3.6 — Unit test
- [x] Task 3.7 — Chạy thật với venv repo

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
