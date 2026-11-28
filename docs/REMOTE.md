# Kết nối agent từ máy khác qua Tailscale

Tài liệu này dành cho **agent** (Claude Code, Cursor, hoặc MCP client bất kỳ) chạy trên máy B,
cần dùng server TQD-VoiceStudio trên máy A (máy có GPU) qua mạng riêng Tailscale — để đọc văn bản
tiếng Việt thành audio, clone giọng và chạy job dài.

> Người đọc nhanh: phần 1–2 là việc của **người** (bật server một lần). Từ phần 3 trở đi là
> tài liệu tham chiếu cho **agent** — có thể dán nguyên vào system prompt hoặc file context
> của agent trên máy B.

---

## 1. Bật server một lần (máy A — người làm)

```bash
tailscale ip -4                    # lấy IP máy A, dạng 100.x.y.z
tqd-voicestudio key new            # tạo API key (hoặc: key show để xem lại)
```

Sửa `host = "0.0.0.0"` trong `config.toml` rồi khởi động lại server (`tqd-voicestudio stop`
rồi chạy bất kỳ lệnh nào, hoặc `tqd-voicestudio serve --host 0.0.0.0`). Server không có key
sẽ từ chối bind ngoài loopback. Mở firewall chỉ cho dải Tailscale:

```powershell
# Windows, PowerShell quyền Administrator
New-NetFirewallRule -DisplayName "TQD-VoiceStudio (Tailscale)" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Any
```

```bash
# Linux (ufw)
sudo ufw allow from 100.64.0.0/10 to any port 8765 proto tcp
```

Không bao giờ mở cổng này ra Internet công cộng. Từ đây gọi `<BASE>` = `http://100.x.y.z:8765`
và `<KEY>` = API key.

## 2. Kiểm chứng kết nối trước khi cấu hình agent

```bash
curl <BASE>/health                          # phải ra 200, không cần key
curl -s -o /dev/null -w "%{http_code}\n" <BASE>/v1/voices     # phải ra 401
curl -H "Authorization: Bearer <KEY>" <BASE>/v1/voices        # phải ra 200 + JSON giọng
```

Smoke test end-to-end (in `REMOTE_MCP_OK` khi đủ chuẩn):

```bash
python scripts/smoke/remote_mcp_smoke.py --url <BASE>/mcp --key <KEY>
```

## 3. Cấu hình MCP cho agent (máy B)

Server nói **MCP Streamable HTTP** tại `<BASE>/mcp`, xác thực `Authorization: Bearer <KEY>`.

**Claude Code:**

```bash
claude mcp add --transport http tqd-voicestudio http://100.x.y.z:8765/mcp \
  --header "Authorization: Bearer <KEY>"
```

**Cursor** (`~/.cursor/mcp.json`):

```json
{"mcpServers": {"tqd-voicestudio": {
  "url": "http://100.x.y.z:8765/mcp",
  "headers": {"Authorization": "Bearer <KEY>"}}}
```

**Client chỉ hỗ trợ stdio** (Claude Desktop…): cài `tqd-voicestudio` trên máy B rồi chạy cầu nối —
`command` = `tqd-voicestudio`, `args` = `["mcp"]`, env:

```
VIZIPVOICE_API_URL=http://100.x.y.z:8765
VIZIPVOICE_API_KEY=<KEY>
```

In sẵn cả 3 dạng cấu hình: `tqd-voicestudio mcp-config --remote http://100.x.y.z:8765`

## 4. Tham chiếu tools cho agent

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
Văn bản dài, nhiều vai, cần phụ đề/nhạc nền: dùng `create_job` hoặc REST API (mục 5).

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

## 5. REST API — khi cần tính năng MCP chưa bao phủ

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

## 6. Cách agent nên dùng (workflow gợi ý)

1. **Câu ngắn, một giọng** → `synthesize_speech`. Chọn giọng bằng cách đọc `description`
   trong `list_voices` thay vì đoán từ tên.
2. **Bài dài / hơn ~300 từ** → `create_job` rồi poll `job_status`; không nhét vào
   `synthesize_speech` (dễ vượt inline 8 MB và timeout client).
3. **Hội thoại nhiều nhân vật** → REST `/v1/audio/speech` với `cast` (xem mục 5).
4. **Clone giọng** → hỏi người dùng xác nhận đồng ý, đọc file clip thành base64, gọi
   `create_voice` kèm `ref_text` nếu có — transcript đúng cho giọng ổn định hơn nhiều.
5. **Sản xuất podcast/video** → job + `normalize=true` + `subtitles` + nhạc nền qua `music`.
6. **Lỗi 401/503** → không retry vô hạn: 401 là sai key (báo người dùng), 503 là server đang
   nạp model (chờ `GET /health` ok rồi gọi lại).

## 7. Tailscale HTTPS (tùy chọn)

Muốn `https://` hợp lệ trong tailnet mà không mở cổng: giữ server ở `127.0.0.1` và

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
# client dùng https://<tên-máy>.<tailnet>.ts.net/mcp
```

Key vẫn nên đặt — HTTPS chỉ mã hoá đường truyền, không thay thế xác thực.

## 8. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `401 invalid or missing bearer token` | Sai key / thiếu header | `tqd-voicestudio key show` ở máy A; kiểm tra `Authorization: Bearer ...` |
| Server không khởi động, mã thoát 2 | Bind ngoài loopback mà chưa có key | `tqd-voicestudio key new` rồi khởi động lại |
| `curl` treo / timeout | Firewall chặn | Rule ở mục 1; `Get-NetFirewallRule` trên Windows |
| Không ping được IP `100.x` | Tailscale offline một đầu | `tailscale status` cả hai máy |
| `Connection refused` | Server chưa chạy hoặc bind `127.0.0.1` | Kiểm tra `host` trong `config.toml` |
| `/mcp` trả 503 | Server đang nạp model | Đợi `GET /health` ra `status: ok` |
| `create_voice` báo thiếu audio | Qua HTTP nhưng gửi `reference_audio_path` | Dùng `audio_base64` + `filename` |
| Audio quá lớn không inline | > 8 MB | Tải qua `download_path` kèm Bearer, hoặc dùng job |
| Claude Desktop không thấy tool | Chưa qua cầu nối stdio | Cấu hình stdio ở mục 3 |

**Bảo mật:** không ghi key vào file nằm trong repo; không gửi key cho bên thứ ba; key chỉ nên
nằm trong cấu hình MCP client của máy B.
