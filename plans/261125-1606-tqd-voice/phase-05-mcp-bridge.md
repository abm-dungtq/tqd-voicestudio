---
phase: 5
title: "MCP stdio qua daemon cục bộ"
status: pending
priority: P1
effort: "2h"
dependencies: [3]
---

> Cwd mặc định: `D:\TTS\tqd-voice` (worktree). `$PY` = `D:/TTS/ViZipvoice/.venv/Scripts/python.exe`. Không sửa file trong `D:\TTS\ViZipvoice`.

# Phase 5: MCP stdio qua daemon cục bộ

## Goal
`tqd-voice mcp` là MCP server stdio mỏng: lần gọi tool đầu tiên tự bật (hoặc dùng lại) server nền `127.0.0.1:8765`, nên mỗi phiên agent không phải nạp lại model. `tqd-voice mcp-config` in sẵn cấu hình cho Claude Desktop, Claude Code và Cursor.

## Context (lead đã xác minh)
- `mcp_server.py` (205 dòng) dùng `from mcp.server.mcpserver import MCPServer` (mcp 2.3.0 trong venv), có 3 tool `list_voices`, `synthesize_speech`, `create_voice` + 1 resource, gọi HTTP qua `_request`. `API_URL` mặc định `http://127.0.0.1:8001`, `OUTPUT_DIR` mặc định `%TEMP%/vizipvoice-mcp`, `SUPPORTED_FORMATS = ("wav", "pcm")`, `create_voice` bắt buộc `ref_text`.
- Server `POST /v1/voices` đã tự cắt đoạn và ASR khi không có transcript, nhận `gender`, `region`.
- Giao thức stdio: stdout chỉ dành cho JSON-RPC; mọi log phải ra stderr (`daemon.ensure_server` đã in ra stderr).
- Giữ tên tool cũ để không phá cấu hình đang dùng.

## Files to Create / Modify
- Modify: `mcp_server.py`, `tqd_voice/cli.py`, `tqd_voice/install.py` (bước 8)
- Create: `scripts/smoke/mcp_smoke.py`, `tests/tqd/test_mcp_server.py`

## Tasks

### Task 5.1 — Nối `mcp_server.py` với daemon
- Target: `mcp_server.py` (`API_URL`, `OUTPUT_DIR`, `_request`, `MCPServer(...)`).
- Steps:
  1. `API_URL`: nếu có env `VIZIPVOICE_API_URL` thì dùng như cũ; nếu không, để `None` và trong `_request`, lần đầu gọi `API_URL = await asyncio.to_thread(daemon.ensure_server, config.load())` (lưu vào biến module, chỉ gọi một lần, có `asyncio.Lock`).
  2. `OUTPUT_DIR`: env `VIZIPVOICE_OUTPUT_DIR` nếu có, ngược lại `paths.outputs_dir() / "mcp"`.
  3. `MCPServer(name="tqd-voice", version=tqd_voice.__version__, instructions=...)`; thêm vào instructions câu: `"Lần gọi đầu tiên có thể mất 1–2 phút để nạp model."`
  4. Không gọi `ensure_server` lúc khởi động (để `initialize` của client không bị quá hạn).
- Verify: phủ bởi Task 5.4.

### Task 5.2 — Sửa tham số tool
- Target: `mcp_server.py` (`synthesize_speech`, `create_voice`, `SUPPORTED_FORMATS`, `_audio_duration_seconds`).
- Steps:
  1. `synthesize_speech`: `voice_id: Optional[str] = None` → dùng `config.load().default_voice` khi `None`. `SUPPORTED_FORMATS = ("wav", "pcm", "mp3", "flac", "opus")`; `duration_seconds` chỉ tính cho wav/pcm, các định dạng khác trả `None`.
  2. `create_voice`: `ref_text: Optional[str] = None` (không gửi trường khi `None` → server tự ASR); thêm `gender: Optional[str] = None` (`male|female`), `region: Optional[str] = None` (`bac|trung|nam`), gửi đúng tên trường Form mà `create_voice` trong `vizipvoice_openai_server.py` dùng.
- Verify: phủ bởi Task 5.3.

### Task 5.3 — Unit test payload
- Target: `tests/tqd/test_mcp_server.py`.
- Steps: monkeypatch `mcp_server._request` bằng hàm async ghi lại `(method, path, kwargs)` và trả `httpx.Response` giả (201, JSON `{"name": "x"}`). Test: (a) `create_voice("x", <file wav tạm>, )` → form data KHÔNG có khóa transcript; (b) có `ref_text="abc"` → có; (c) `gender="female", region="nam"` có mặt trong form.
- Verify: `PYTHONUTF8=1 $PY -m pytest tests/tqd/test_mcp_server.py -q` exit 0.

### Task 5.4 — Lệnh `mcp` và `mcp-config`
- Target: `tqd_voice/cli.py`, `tqd_voice/install.py`.
- Steps:
  1. `tqd-voice mcp`: `import mcp_server; mcp_server.main()`. Không in gì ra stdout trước đó.
  2. `tqd-voice mcp-config [--client {claude-desktop,cursor,claude-code}]` (mặc định `claude-desktop`): `exe = shutil.which("tqd-voice") or sys.argv[0]` (đường dẫn tuyệt đối). `claude-desktop`/`cursor` → in JSON `{"mcpServers": {"tqd-voice": {"command": exe, "args": ["mcp"]}}}` (indent 2, `ensure_ascii=False`) kèm một dòng nói file cấu hình nằm đâu (Claude Desktop: `%APPDATA%\Claude\claude_desktop_config.json` trên Windows, `~/.config/Claude/claude_desktop_config.json` trên Linux; Cursor: `~/.cursor/mcp.json`). `claude-code` → in `claude mcp add tqd-voice -- "<exe>" mcp`.
  3. `install.py` bước 8: in kết quả `mcp-config` mặc định.
- Verify: `PYTHONUTF8=1 D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe mcp-config` exit 0 và stdout parse được bằng `json.loads` sau khi bỏ dòng ghi chú (dòng ghi chú in ra stderr để stdout là JSON thuần).

### Task 5.5 — Smoke test qua stdio
- Target: `scripts/smoke/mcp_smoke.py`.
- Steps: dùng client của chính gói `mcp` đã cài (kiểm tra API thật trong `.venv/Lib/site-packages/mcp/client/` — `stdio_client`, `ClientSession`, `StdioServerParameters` hoặc tên tương đương trong 2.3.0; không đoán). Script nhận `--exe` (mặc định `shutil.which("tqd-voice")`): mở phiên stdio `[exe, "mcp"]` với env hiện tại; `initialize`; kiểm tra `list_tools` có đủ `list_voices`, `synthesize_speech`, `create_voice`; gọi `list_voices` (kết quả chứa `Đinh-Quyết`); gọi `synthesize_speech` với `text="Xin chào, đây là thử nghiệm MCP của TQD Voice."`, parse JSON trả về, kiểm tra `path` tồn tại và `size_bytes > 10000`; in `MCP_SMOKE_OK <path>`; timeout tổng 400 s; lỗi → exit 1.
- Verify: với `PYTHONUTF8=1 TQD_VOICE_HOME=D:/TTS/tqd-install-home`: `$PY scripts/smoke/mcp_smoke.py --exe D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe` exit 0 và in dòng bắt đầu bằng `MCP_SMOKE_OK`. Sau đó `D:/TTS/ViZipvoice/.venv/Scripts/tqd-voice.exe stop`.
- Commit: "MCP stdio bridge backed by local tqd-voice daemon".

## Todo
- [ ] Task 5.1 — Nối `mcp_server.py` với daemon
- [ ] Task 5.2 — Sửa tham số tool
- [ ] Task 5.3 — Unit test payload
- [ ] Task 5.4 — Lệnh `mcp` và `mcp-config`
- [ ] Task 5.5 — Smoke test qua stdio

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
