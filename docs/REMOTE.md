# Kết nối agent từ máy khác qua Tailscale

Tài liệu này dành cho **agent** (Claude Code, Cursor, hoặc MCP client bất kỳ) chạy trên máy B,
cần dùng server TQD-VoiceStudio trên máy A (máy có GPU) qua mạng riêng Tailscale — để đọc văn bản
tiếng Việt thành audio, clone giọng và chạy job dài.

> Phần 1–3 là việc của **người** trên máy A (làm một lần). Từ phần 4 trở đi là tài liệu tham
> chiếu cho **agent** — có thể dán nguyên vào system prompt hoặc file context của agent trên máy B.

Ký hiệu dùng xuyên suốt: `<BASE>` = địa chỉ server, `<KEY>` = API key.

---

## 1. Bật server một lần (máy A)

```bash
tailscale ip -4                    # IP máy A, dạng 100.x.y.z
tqd-voicestudio key new            # tạo API key (key show để xem lại)
```

Sửa `host = "0.0.0.0"` trong `config.toml` rồi khởi động lại server. Server không có key sẽ
từ chối bind ngoài loopback. **Cơ chế:** `tqd-voicestudio serve` đọc `config.toml` và xuất ra
biến môi trường `PORT_HOST`/`PORT`/`VIZIPVOICE_API_KEY` cho tiến trình server — nếu chạy
`vizipvoice_openai_server.py` trực tiếp (dev) thì phải tự set 3 biến đó, không là server rơi về
mặc định `127.0.0.1:8001`.

## 2. Khuyến nghị chính: đi qua HTTPS của Tailscale Serve

Đây là đường kết nối **đáng tin nhất**, đã kiểm chứng thực tế:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
```

- `<BASE>` = `https://<tên-máy>.<tailnet>.ts.net` (ví dụ `https://xabm-pc.tail496c36.ts.net`)
- Tailscale terminate TLS rồi forward vào `127.0.0.1` — traffic không đi qua Windows Firewall
  inbound nên **không cần mở rule**, đồng thời có mã hoá đầu-cuối-trong-tailnet.
- Với cách này server chỉ cần bind `127.0.0.1` là đủ cho agent — vẫn nên đặt key vì HTTPS chỉ
  mã hoá đường truyền, không thay thế xác thực. Bind `0.0.0.0` chỉ khi còn muốn cho truy cập
  IP trực tiếp (mục 3).

Kiểm tra từ máy B:

```bash
curl https://<tên-máy>.<tailnet>.ts.net/health      # phải ra 200, không cần key
```

Nếu ra `502`: serve đang trỏ sai cổng backend (`tailscale serve status` để xem) hoặc server chưa chạy.

## 3. Phương án phụ: IP trực tiếp + firewall

Nếu muốn `<BASE>` = `http://100.x.y.z:8765` (HTTP trần trong tailnet): server bắt buộc bind
`0.0.0.0` hoặc IP tailscale, và phải mở firewall **chỉ cho dải Tailscale**:

```powershell
# Windows, PowerShell quyền Administrator
New-NetFirewallRule -DisplayName "TQD-VoiceStudio (Tailscale)" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Any
```

```bash
# Linux (ufw)
sudo ufw allow from 100.64.0.0/10 to any port 8765 proto tcp
```

Không bao giờ mở cổng này ra Internet công cộng. Kiểm chứng từ máy B:

```bash
curl <BASE>/health                                            # 200, không cần key
curl -s -o /dev/null -w "%{http_code}\n" <BASE>/v1/voices     # 401 (auth bật đúng)
curl -H "Authorization: Bearer <KEY>" <BASE>/v1/voices        # 200 + JSON giọng
```

Smoke test end-to-end đầy đủ (in `REMOTE_MCP_OK` khi đủ chuẩn — kiểm tra cả auth, list voices,
audio inline và tải history):

```bash
python scripts/smoke/remote_mcp_smoke.py --url <BASE>/mcp --key <KEY>
```

## 4. Tự động chạy server khi máy A khởi động

**Windows (không cần quyền admin):** tạo file `tqd-voicestudio.vbs` trong
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup` với nội dung:

```vbscript
CreateObject("Wscript.Shell").Run "D:\path\to\start-server.cmd", 0, False
```

trong đó `start-server.cmd` set đủ biến môi trường (`PORT_HOST`, `PORT`, `VIZIPVOICE_API_KEY`,
đường dẫn model/voices nếu dev) rồi gọi python chạy server, redirect log ra file. Cách này chạy
ẩn khi user đăng nhập, không cần admin (khác với `schtasks /sc onlogon` vốn yêu cầu elevate
trên nhiều máy).

**Linux:** systemd user unit (`systemctl --user`) với `Restart=on-failure`, hoặc `@reboot`
trong cron kèm `tailscaled` đã bật.

## 5. Cấu hình MCP cho agent (máy B)

Server nói **MCP Streamable HTTP** tại `<BASE>/mcp`, xác thực `Authorization: Bearer <KEY>`.

**Claude Code:**

```bash
claude mcp add --transport http tqd-voicestudio <BASE>/mcp \
  --header "Authorization: Bearer <KEY>"
```

**Cursor** (`~/.cursor/mcp.json`):

```json
{"mcpServers": {"tqd-voicestudio": {
  "url": "<BASE>/mcp",
  "headers": {"Authorization": "Bearer <KEY>"}}}
```

**Client chỉ hỗ trợ stdio** (Claude Desktop…): cài `tqd-voicestudio` trên máy B rồi chạy cầu nối —
`command` = `tqd-voicestudio`, `args` = `["mcp"]`, env:

```
VIZIPVOICE_API_URL=<BASE>
VIZIPVOICE_API_KEY=<KEY>
```

In sẵn cả 3 dạng cấu hình: `tqd-voicestudio mcp-config --remote <BASE>`

## 6. Tham chiếu tools cho agent

Server có **5 tools**. Lần gọi đầu tiên sau khi server khởi động có thể mất 1–2 phút nạp model —
agent nên báo người dùng chờ thay vì retry.

### `list_voices() -> JSON string`

Toàn bộ giọng đọc được: `id/name` dùng làm `voice_id`, kèm `gender`, `region`, `kind`
(`preset` = có sẵn, `custom` = clone), `description` (gợi ý tính cách giọng — đọc để chọn giọng
hợp nội dung). Cũng có sẵn như resource `vizipvoice://voices`.

### `synthesize_speech(text, voice_id?, speed?, style?, sample_rate?, response_format?, return_base64?)`

Đọc văn bản tiếng Việt thành audio. Ràng buộc:

- `speed`: 0.5–2.0, mặc định theo giọng.
- `sample_rate`: một trong 8000/16000/24000/48000 (mặc định 48000).
- `response_format`: `wav` | `mp3` | `flac` | `opus` | `pcm`.
- Kết quả qua HTTP: **audio inline dạng AudioContent nếu ≤ 8 MB**; lớn hơn thì chỉ có
  `download_path` = `/v1/history/<id>/audio` — tải bằng GET kèm Bearer key.
- Text block kèm theo luôn có `size_bytes`, `duration_seconds`, `history_id`.

**Giới hạn quan trọng:** tool này là đọc đồng bộ một giọng — phù hợp văn bản ngắn (vài câu).
Văn bản dài, nhiều vai, cần phụ đề/nhạc nền: dùng `create_job` hoặc REST API (mục 7).

### `create_job(text, voice_id?, response_format?, speed?, normalize?, trim_silence?, music_id?, webhook_url?)`

Đọc văn bản dài ở chế độ nền — trả về `{id, status}` ngay. Server tự tách câu, đọc tuần tự,
ghép lại thành một file. Job **sống sót qua khởi động lại server** và chạy tiếp từ đoạn dở.

- `normalize=true`: chuẩn hoá âm lượng −16 LUFS (chuẩn podcast).
- `trim_silence=true`: cắt khoảng lặng thừa.
- `webhook_url`: POST `job.done`/`job.failed`/`job.canceled` kèm chữ ký HMAC-SHA256 ở header
  `X-TQD-Signature` (dạng `sha256=<hex>`, ký trên raw body), thử lại 3 lần. Secret riêng,
  tự sinh và lưu ở `webhook_secret` trong `config.toml` trên máy A — không phải API key.

### `job_status(job_id, save_audio?)`

Trạng thái job: `queued|running|done|failed|canceled`, phần trăm theo số đoạn xong,
`audio_url`, `subtitles_srt_url`/`subtitles_vtt_url` khi có. Agent nên poll mỗi 5–15 giây
thay vì liên tục. `save_audio=true` chỉ có tác dụng qua stdio (HTTP client tự tải qua
`audio_url` kèm Bearer).

### `create_voice(name, consent, ref_text?, gender?, region?, description?, audio_base64?, filename?, denoise?, force?)`

Clone giọng mới từ clip mẫu. **Bắt buộc `consent=true`** — chỉ clone khi chủ giọng đồng ý;
agent phải xác nhận điều này với người dùng trước khi gọi.

- Qua HTTP **bắt buộc** gửi `audio_base64` + `filename` (server không đọc được file trên máy B);
  tối đa 25 MB, nên là wav/mp3/flac 3–10 giây, rõ lời, ít ồn.
- `ref_text` = transcript đúng của clip; bỏ trống thì server tự nhận dạng (kém chính xác hơn).
- `gender`: `male`|`female`; `region`: `bac`|`trung`|`nam`.
- Sau khi tạo xong, giọng dùng được ngay qua `voice_id=name` và xuất hiện trong `list_voices`.
  `force=true` để ghi đè giọng clone cùng tên (cache các bản đọc cũ bằng giọng đó tự mất hiệu lực).

## 7. REST API — khi cần tính năng MCP chưa bao phủ

Mọi endpoint (trừ `GET /health`) cần `Authorization: Bearer <KEY>`. JSON in/out.
Một số việc chỉ làm được qua REST:

| Việc | Endpoint | Ghi chú |
|---|---|---|
| Đọc nhiều vai | `POST /v1/audio/speech` với `cast` | Body thêm `"cast": {"Vai": "voice_id"}`; văn bản dạng `Vai: lời thoại` mỗi dòng |
| Phụ đề SRT/VTT | `POST /v1/audio/speech/timed` | Trả audio + timing; hoặc lấy từ job: `GET /v1/jobs/{id}/subtitles.srt` |
| Chèn ngắt nghỉ | thẻ `[pause:1.5]` trong input | Giây, 0.1–10 |
| Từ điển phát âm | `GET`/`PUT /v1/lexicon` | `{"AI": "ây ai"}` — áp cho mọi lần đọc sau |
| Nhạc nền | `POST /v1/music` (upload), truyền `music` trong speech/job | `volume_db` ≈ −20, `duck_db` ≈ −12 là khởi đầu tốt |
| Lịch sử/cache | `GET /v1/history`, `GET /v1/history/{id}/audio` | Cùng input+giọng+cấu hình trả cache gần như ngay |
| QC tự động | truyền `quality_check: true` | Server nghe lại bằng ASR, đọc lại đoạn sai; xem header `X-QC-*` |
| Job nền | `POST /v1/jobs`, `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel` | — |
| Clone giọng | `POST /v1/voices` (multipart) | Cần `consent=true`; file ≤ 25 MB |

Ví dụ đọc nhiều vai bằng curl:

```bash
curl -X POST <BASE>/v1/audio/speech \
  -H "Authorization: Bearer <KEY>" -H "Content-Type: application/json" \
  -d '{"input":"Lan: Chào em.\nNam: Chào chị Lan.","cast":{"Lan":"Bách-Việt","Nam":"Cao-Việt"},"response_format":"mp3"}' \
  --output hoi-thoai.mp3
```

## 8. Cách agent nên dùng (workflow gợi ý)

1. **Câu ngắn, một giọng** → `synthesize_speech`. Chọn giọng bằng cách đọc `description`
   trong `list_voices` thay vì đoán từ tên.
2. **Bài dài / hơn ~300 từ** → `create_job` rồi poll `job_status`; không nhét vào
   `synthesize_speech` (dễ vượt inline 8 MB và timeout client).
3. **Hội thoại nhiều nhân vật** → REST `/v1/audio/speech` với `cast` (xem mục 7).
4. **Clone giọng** → hỏi người dùng xác nhận đồng ý, đọc file clip thành base64, gọi
   `create_voice` kèm `ref_text` nếu có — transcript đúng cho giọng ổn định hơn nhiều.
5. **Sản xuất podcast/video** → job + `normalize=true` + `subtitles` + nhạc nền qua `music`.
6. **Lỗi 401/503** → không retry vô hạn: 401 là sai key (báo người dùng), 503 là server đang
   nạp model (chờ `GET /health` ok rồi gọi lại).

## 9. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| HTTPS `ts.net` trả **502** | Serve trỏ sai cổng backend, hoặc server chưa chạy | `tailscale serve status` trên máy A; kiểm tra `curl 127.0.0.1:8765/health` tại máy A |
| `http://100.x:8765` **timeout** (không refused) | Windows Firewall drop gói inbound | Rule ở mục 3 — hoặc chuyển sang đường HTTPS (mục 2) không cần rule |
| `Connection refused` | Server chưa chạy / bind sai | Kiểm tra process và `netstat` trên máy A |
| Server chết sau khi terminal đóng | Chạy như tiến trình con của shell | Auto-start theo mục 4 |
| Server bind nhầm `127.0.0.1:8001` | Chạy script trực tiếp mà thiếu env | Set `PORT`/`PORT_HOST`/`VIZIPVOICE_API_KEY` (mục 1) hoặc chạy qua CLI |
| `401 invalid or missing bearer token` | Sai key / thiếu header | `tqd-voicestudio key show` ở máy A |
| `/mcp` trả 503 | Server đang nạp model | Đợi `GET /health` ra `status: ok` |
| Không ping được IP `100.x` | Tailscale offline một đầu | `tailscale status` cả hai máy |
| `create_voice` báo thiếu audio | Qua HTTP nhưng gửi `reference_audio_path` | Dùng `audio_base64` + `filename` |
| Audio quá lớn không inline | > 8 MB | Tải qua `download_path` kèm Bearer, hoặc dùng job |

**Bảo mật:** không ghi key vào file nằm trong repo; không gửi key cho bên thứ ba; key chỉ nên
nằm trong cấu hình MCP client của máy B.
