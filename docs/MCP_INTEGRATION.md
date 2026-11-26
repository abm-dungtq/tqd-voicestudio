# Tích hợp MCP + Tailscale cho ViZipVoice

Tài liệu này mô tả cách bật speech server của ViZipVoice và nối nó vào agent qua MCP
(Model Context Protocol), kể cả khi máy GPU và máy agent nằm ở hai nơi khác nhau
trong cùng một tailnet của Tailscale. Hướng dẫn mở server cho máy khác (API key,
tường lửa, kiểm tra) nằm ở [REMOTE.md](REMOTE.md) — tài liệu này tập trung phần MCP.

## Kiến trúc

Có hai cách nối MCP:

**A. stdio cục bộ** — MCP client spawn `tqd-voicestudio mcp` (hoặc `python mcp_server.py`)
trên máy agent; bridge gọi speech server qua HTTP:

```text
MCP client (Claude Desktop / IDE agent)
        │  stdio (JSON-RPC)
        ▼
mcp_server.py  (chạy trên máy agent)
        │  HTTP + Bearer token
        ▼
vizipvoice_openai_server.py  (máy GPU)
        └── Tailscale chỉ thay đường mạng giữa hai máy, không đổi API
```

**B. Streamable HTTP tại `/mcp`** — speech server tự mount MCP endpoint ngay bên trong
(không cần process bridge, không cần cài gì trên máy agent):

```text
MCP client (máy khác trong tailnet)
        │  MCP Streamable HTTP, Authorization: Bearer <API key>
        ▼
http://<máy GPU>:8765/mcp   (cùng process với speech server)
```

Ở chế độ B, audio được trả **inline** dưới dạng MCP `AudioContent` (base64) khi file
≤ 8 MB thay vì ghi ra đĩa — vì client ở máy khác không đọc được đường dẫn local.
`create_voice` cũng khác: qua HTTP không dùng được `reference_audio_path`, phải gửi
`audio_base64` + `filename` (tối đa 25 MB). In cấu hình sẵn cho client bằng
`tqd-voicestudio mcp-config --remote http://100.x.y.z:8765`.

- `vizipvoice_openai_server.py`: OpenAI-compatible API, expose `/health`, `/v1/models`,
  `/v1/voices`, `/v1/audio/speech`, `/v1/jobs`..., đồng thời mount MCP tại `/mcp`.
- `mcp_server.py`: cùng một bộ tool, phục vụ qua stdio (`tqd-voicestudio mcp`) hoặc
  Streamable HTTP (mount trong speech server).
- Tailscale: dùng IP tailnet hoặc `tailscale serve` để máy agent gọi được máy GPU.

## 1. Cài đặt

```bash
cd ViZipvoice

# Model: server mặc định đọc <thư mục cha của repo>/models/ViZipvoice
# (script tính ROOT = parent của thư mục chứa file server).
# Repo tại D:/TTS/ViZipvoice => mặc định D:/TTS/models/ViZipvoice.
hf download contextboxai/ViZipvoice --local-dir ../models/ViZipvoice
# Nếu muốn giữ model trong repo thì tải vào models/ViZipvoice và trỏ biến:
# hf download contextboxai/ViZipvoice --local-dir models/ViZipvoice
# export VIZIPVOICE_MODEL_DIR=$PWD/models/ViZipvoice

# Runtime của speech server (torch, fastapi, uvicorn, ...)
pip install -r requirements.txt -r requirements-service.txt

# Runtime của MCP bridge (MCP Python SDK v2)
pip install -r requirements-mcp.txt
```

`requirements-mcp.txt` pin `mcp>=2.3`: từ MCP SDK v2, `FastMCP` đã đổi tên thành
`MCPServer` (`mcp.server.mcpserver`). `mcp_server.py` viết theo API v2; nếu buộc phải
dùng `mcp<2` thì phải sửa import, không chỉ đổi version.

## 2. Bật speech server

```bash
export VIZIPVOICE_API_KEY=doi_thanh_key_cua_ban   # bỏ trống = tắt auth
export PORT=8001
export PORT_HOST=127.0.0.1                         # chỉ localhost
python vizipvoice_openai_server.py
```

Biến môi trường liên quan:

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `VIZIPVOICE_API_KEY` | rỗng | Bearer token cho mọi route `/v1/*`. Rỗng = không kiểm tra auth |
| `PORT` | `8001` | Port HTTP (8000 thuộc VieNeu-TTS trên máy này) |
| `PORT_HOST` | `127.0.0.1` | Interface lắng nghe; đặt IP Tailscale khi muốn truy cập từ xa |
| `VIZIPVOICE_MODEL_DIR` | `<thư mục cha của repo>/models/ViZipvoice` | Thư mục model + `audio/` chứa preset voice; đặt biến nếu model nằm trong repo |
| `VIZIPVOICE_MAX_STREAMS` | `4` | Số request synthesize song song |
| `VIZIPVOICE_QUEUE_TIMEOUT` | `180` | Giây chờ tối đa trong hàng đợi |

Trên máy này port 8000 thuộc VieNeu-TTS, nên mặc định dùng `PORT=8001`
(`start-api.cmd` đặt sẵn). Nếu port 8001 cũng bị chiếm (`[Errno 10048]`), đổi
`PORT=8100` và dùng đúng port đó ở mọi bước sau.

Kiểm tra:

```bash
curl http://127.0.0.1:8001/health
# {"status":"ok","backend":"vizipvoice-fp16",...,"voices":<so_voice>}

curl -H "Authorization: Bearer $VIZIPVOICE_API_KEY" http://127.0.0.1:8001/v1/models

# Không có key phải trả 401
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/v1/models
```

Synthesize thử bằng curl:

```bash
curl -X POST http://127.0.0.1:8001/v1/audio/speech \
  -H "Authorization: Bearer $VIZIPVOICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input":"Xin chào, đây là bản kiểm tra ViZipVoice.","voice":"Đinh-Quyết","response_format":"wav"}' \
  -o out.wav
```

Lần khởi động đầu tiên có thể chậm hơn: server tải vocoder (`charactr/vocos-mel-24khz`)
và pre-warm model trước khi nhận request; hãy đợi log `ViZipVoiceEngine ready`.

## 3. Chạy MCP bridge

### Cách nhanh: `tqd-voicestudio mcp` (daemon cục bộ)

Sau khi cài bằng `uv tool install` và chạy `tqd-voicestudio install`, MCP client chỉ cần spawn
`tqd-voicestudio mcp`. Ở lần gọi tool đầu tiên, bridge tự bật (hoặc dùng lại) speech server nền
tại `127.0.0.1:8765` (đọc `config.toml` trong `TQD_VOICESTUDIO_HOME`), nên các phiên sau không
phải nạp lại model. Lần đầu có thể mất 1–2 phút. Trên Windows, daemon được tạo qua WMI để
không bị Job Object của MCP client kill khi đóng phiên.

```bash
tqd-voicestudio mcp-config                       # JSON cho Claude Desktop (mặc định)
tqd-voicestudio mcp-config --client cursor       # JSON cho Cursor
tqd-voicestudio mcp-config --client claude-code  # lệnh `claude mcp add ...`
tqd-voicestudio stop                             # tắt daemon
```

Đặt `VIZIPVOICE_API_URL` để bỏ qua daemon và dùng một speech server có sẵn (ví dụ
`http://127.0.0.1:8001` hoặc URL Tailscale ở mục 4).

### Chạy trực tiếp `mcp_server.py`

`mcp_server.py` chạy bằng stdio — MCP client sẽ tự spawn process này.

```bash
export VIZIPVOICE_API_URL=http://127.0.0.1:8001   # hoặc URL Tailscale ở mục 4
export VIZIPVOICE_API_KEY=doi_thanh_key_cua_ban   # phải khớp key của server
export VIZIPVOICE_OUTPUT_DIR=/tmp/vizipvoice-mcp  # nơi lưu file audio (tùy chọn)
python mcp_server.py
```

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `VIZIPVOICE_API_URL` | không đặt = daemon `127.0.0.1:8765` | Base URL của speech server |
| `VIZIPVOICE_API_KEY` | rỗng | Bearer token; rỗng thì không gửi header `Authorization` |
| `VIZIPVOICE_OUTPUT_DIR` | `<TQD_VOICESTUDIO_HOME>/outputs/mcp` | Nơi ghi file audio trả về |

### Tool và resource

| Tên | Loại | Tham số chính |
|-----|------|---------------|
| `list_voices` | tool | — (trả JSON danh sách voice, alias, `recommended_speed`) |
| `synthesize_speech` | tool | `text`, `voice_id`, `speed` (0.5–2.0), `style`, `sample_rate` (8000/16000/24000/48000), `response_format` (`wav`/`pcm`/`mp3`/`flac`/`opus`), `return_base64`; `voice_id` bỏ trống = giọng mặc định trong `config.toml` |
| `create_voice` | tool | `name`, `reference_audio_path` (stdio: file local trên máy chạy MCP) **hoặc** `audio_base64` + `filename` (bắt buộc khi qua HTTP, tối đa 25 MB), `ref_text` (tùy chọn; bỏ trống thì server tự nhận dạng), `gender`, `region`, `description`, `denoise`, `force` |
| `create_job` | tool | `text`, `voice_id`, `response_format`, `speed`, `normalize`, `trim_silence`, `music_id`, `webhook_url` — gửi văn bản dài thành job nền, trả `{"id", "status"}` |
| `job_status` | tool | `job_id`, `save_audio` — trạng thái job (progress, url); `save_audio=true` (chỉ stdio) tải file về `outputs/mcp` khi job xong |
| `vizipvoice://voices` | resource | — (catalog voice dạng JSON) |

`synthesize_speech` trả JSON: `path`, `size_bytes`, `duration_seconds`, `voice`,
`format`, `sample_rate`; thêm `audio_base64` nếu `return_base64=true`. Khi chạy trong
speech server (Streamable HTTP tại `/mcp`), tool không ghi file mà trả audio inline
dạng MCP `AudioContent` cho file ≤ 8 MB — client máy khác nghe/lưu trực tiếp.
`create_voice` enroll voice từ clip 3–10 giây, transcript phải khớp audio; file được lưu
vào `voices/` dưới ROOT của server (`<thư mục cha của repo>/voices/`) và tự nạp lại ở lần
khởi động sau. `create_job`/`job_status` bọc API `/v1/jobs` — xem vòng đời job, webhook
và phụ đề ở [TQD_VOICESTUDIO.md](TQD_VOICESTUDIO.md).

### MCP qua Streamable HTTP (remote)

Speech server mount sẵn MCP endpoint tại `/mcp` (cùng port với REST API). Khi server có
API key thì `/mcp` cũng bắt buộc `Authorization: Bearer <key>` như mọi route `/v1/*`.
Không cần cài Python/bridge trên máy agent — chỉ cần client hỗ trợ MCP Streamable HTTP
(Claude Code, Cursor bản mới...). In cấu hình mẫu:

```bash
tqd-voicestudio mcp-config --remote http://100.x.y.z:8765            # key để dạng <KEY>, tự thay sau
tqd-voicestudio mcp-config --remote http://100.x.y.z:8765 --show-key  # nhúng key thật từ config cục bộ
tqd-voicestudio mcp-config --remote http://100.x.y.z:8765 --key-env TQD_KEY   # đọc key từ biến môi trường lúc chạy
```

`mcp-config --remote` in ra ba cách: lệnh `claude mcp add --transport http` cho Claude Code,
JSON cho Cursor (`~/.cursor/mcp.json`), và cầu nối stdio cho Claude Desktop. Cấu hình Cursor
tương đương viết tay:

```json
{
  "mcpServers": {
    "tqd-voicestudio": {
      "url": "http://100.x.y.z:8765/mcp",
      "headers": {"Authorization": "Bearer <KEY>"}
    }
  }
}
```

Client không hỗ trợ HTTP thì dùng cầu nối stdio: spawn `tqd-voicestudio mcp` (hoặc
`python mcp_server.py`) với env `VIZIPVOICE_API_URL=http://100.x.y.z:8765` và
`VIZIPVOICE_API_KEY=<KEY>` — tool vẫn gọi đúng server từ xa qua REST, audio ghi ra file
trên máy agent.

Điều kiện: server phải nghe trên interface tailnet (`host` trong `config.toml`) và đã có
API key — xem đầy đủ các bước ở [REMOTE.md](REMOTE.md). Không nên đưa `/mcp` ra Internet
công cộng.

## 4. Tailscale

Mục tiêu: máy agent gọi được speech server trên máy GPU mà không mở port ra Internet.
Cài Tailscale trên cả hai máy và đăng nhập cùng một tailnet (`tailscale up`).

### Cách A — bind trực tiếp IP Tailscale (đơn giản)

```bash
# Máy GPU
tailscale ip -4                      # ví dụ 100.101.102.103
export PORT_HOST=100.101.102.103
export PORT=8001
python vizipvoice_openai_server.py

# Máy agent
curl http://100.101.102.103:8001/health
export VIZIPVOICE_API_URL=http://100.101.102.103:8001
```

Server chỉ nghe trên interface tailnet, không lộ ra LAN/Internet.

### Cách B — `tailscale serve` (HTTPS trong tailnet, khuyến nghị)

```bash
# Máy GPU: server vẫn giữ 127.0.0.1:8001
export PORT_HOST=127.0.0.1
python vizipvoice_openai_server.py

# Terminal khác trên máy GPU
tailscale serve --bg 8001
tailscale serve status               # in ra URL dạng https://<may>.<tailnet>.ts.net
```

```bash
# Máy agent
curl https://<may>.<tailnet>.ts.net/health
export VIZIPVOICE_API_URL=https://<may>.<tailnet>.ts.net
```

`tailscale serve` cần bật HTTPS certificates + MagicDNS trong admin console; traffic chỉ
đi trong tailnet. Không dùng `tailscale funnel` trừ khi thật sự muốn public ra Internet.

### Cấu hình MCP client (Claude Desktop)

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vizipvoice": {
      "command": "D:/TTS/ViZipvoice/.venv/Scripts/python.exe",
      "args": ["D:/TTS/ViZipvoice/mcp_server.py"],
      "env": {
        "VIZIPVOICE_API_URL": "https://<may>.<tailnet>.ts.net",
        "VIZIPVOICE_API_KEY": "doi_thanh_key_cua_ban"
      }
    }
  }
}
```

macOS/Linux: đổi `command` thành `/duong/dan/ViZipvoice/.venv/bin/python` và `args` thành
đường dẫn tuyệt đối tới `mcp_server.py`. IDE agent khác dùng cùng cơ chế stdio
(command + args + env).

## 5. Kiểm tra nhanh toàn tuyến

```bash
# 1) API trực tiếp
curl -H "Authorization: Bearer $VIZIPVOICE_API_KEY" "$VIZIPVOICE_API_URL/v1/voices"

# 2) MCP bridge (spawn stdio, gọi tool)
python - <<'PY'
import asyncio, json, os
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession

async def main():
    env = {**os.environ}
    params = StdioServerParameters(command="python", args=["mcp_server.py"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])
            out = await s.call_tool("synthesize_speech", {
                "text": "Xin chào từ MCP.", "voice_id": "Đinh-Quyết"
            }, read_timeout_seconds=300)
            print(out.content[0].text)

asyncio.run(main())
PY
```

## 6. Xử lý lỗi

| Hiện tượng | Nguyên nhân / cách xử lý |
|------------|--------------------------|
| `[Errno 10048] only one usage of each socket address` | Port đã bị chiếm. Đổi `PORT` (ví dụ 8100) và cập nhật `VIZIPVOICE_API_URL` |
| `401 invalid or missing bearer token` | Key của MCP không khớp `VIZIPVOICE_API_KEY` của server (hoặc server có key mà client gửi rỗng) |
| `unknown voice 'X'; see GET /v1/voices` | Sai `voice_id`; gọi `list_voices` để lấy tên/alias đúng |
| `Missing ViZipVoice model file(s)` | Chưa tải model hoặc sai `VIZIPVOICE_MODEL_DIR` |
| `cannot reach ViZipVoice server at ...` | Server chưa bật, sai port/URL, hoặc Tailscale chưa kết nối (`tailscale status`) |
| `Connection refused` qua tailnet | Server đang bind `127.0.0.1`; dùng `PORT_HOST=<IP tailscale>` hoặc `tailscale serve` |
| Khởi động lâu (30–60 giây) | Đang tải vocoder/checkpoint và pre-warm; chờ log `ViZipVoiceEngine ready` trước khi gửi request |
| `sample_rate must be one of ...` | Chỉ hỗ trợ 8000/16000/24000/48000 |

## 7. Bảo mật

- Luôn đặt `VIZIPVOICE_API_KEY` khi server nghe ra ngoài localhost; key nằm trong env của
  MCP client, không commit vào repo.
- Giới hạn ai vào được port bằng Tailscale ACL (device/tag) — tailnet là lớp mạng, API key
  là lớp xác thực ứng dụng; cần cả hai.
- Không dùng `tailscale funnel` (public Internet) cho endpoint TTS trừ khi có yêu cầu rõ
  ràng; nếu buộc phải public, thêm rate limit/reverse proxy và key riêng.
- `create_voice` nhận `reference_audio_path` là file trên máy chạy MCP — không phải upload
  từ xa; chỉ dùng với clip bạn có quyền sử dụng.
