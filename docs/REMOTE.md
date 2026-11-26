# Dùng TQD-VoiceStudio từ máy khác (Tailscale)

Hướng dẫn này cho phép máy B (laptop, máy làm việc) dùng server TQD-VoiceStudio chạy trên máy A có GPU,
qua mạng riêng Tailscale. Server mặc định chỉ nghe `127.0.0.1`; mở ra ngoài **bắt buộc có API key**.

## 1. Bật server cho máy khác (máy A)

1. Cài Tailscale trên cả hai máy, đăng nhập cùng một tailnet. Lấy IP Tailscale của máy A:
   `tailscale ip -4` (dạng `100.x.y.z`).
2. Tạo API key (chỉ hiện một lần, được lưu vào `config.toml`):

   ```bash
   tqd-voicestudio key new
   tqd-voicestudio key show        # xem lại khi cần
   ```

3. Cho server nghe trên mọi giao diện, hoặc chỉ trên IP Tailscale, rồi khởi động lại:

   ```bash
   tqd-voicestudio stop
   # sửa config.toml:  host = "0.0.0.0"   (hoặc host = "100.x.y.z")
   tqd-voicestudio say "kiểm tra"     # lệnh này tự bật lại server nền với cấu hình mới
   ```

   Hoặc chạy trực tiếp: `tqd-voicestudio serve --host 0.0.0.0`. Nếu `host` không phải loopback mà chưa có
   key, server từ chối khởi động (mã thoát 2) và nhắc `key new`.

Khi có key, mọi đường dẫn đều cần `Authorization: Bearer <key>`, trừ `GET /health`. Các lệnh `say`,
`clone`, `voices`, `jobs`... trên chính máy A tự gửi key từ `config.toml`.

## 2. Tường lửa

**Windows (PowerShell chạy quyền Administrator)** — chỉ cho dải Tailscale vào cổng 8765:

```powershell
New-NetFirewallRule -DisplayName "TQD-VoiceStudio (Tailscale)" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Profile Any
```

**Linux (ufw):**

```bash
sudo ufw allow in on tailscale0 to any port 8765 proto tcp
# hoặc theo dải địa chỉ:
sudo ufw allow from 100.64.0.0/10 to any port 8765 proto tcp
```

Đừng mở cổng này ra Internet công cộng.

## 3. Kiểm tra kết nối (máy B)

```bash
curl http://100.x.y.z:8765/health                                   # mở, không cần key
curl -H "Authorization: Bearer <KEY>" http://100.x.y.z:8765/v1/voices
curl -s -o /dev/null -w "%{http_code}\n" http://100.x.y.z:8765/v1/voices   # phải ra 401
```

Smoke test đầy đủ (kiểm tra 401, `list_voices`, âm thanh inline, tải lại từ lịch sử):

```bash
python scripts/smoke/remote_mcp_smoke.py --url http://100.x.y.z:8765/mcp --key <KEY>
# in ra REMOTE_MCP_OK khi thành công
```

## 4. Cấu hình client

In sẵn cấu hình bằng (thay `<KEY>` bằng key thật, hoặc dùng `--key-env NAME` / `--show-key`):

```bash
tqd-voicestudio mcp-config --remote http://100.x.y.z:8765
```

- **Claude Code** (HTTP):
  `claude mcp add --transport http tqd-voicestudio http://100.x.y.z:8765/mcp --header "Authorization: Bearer <KEY>"`
- **Cursor** (`~/.cursor/mcp.json`):
  `{"mcpServers": {"tqd-voicestudio": {"url": "http://100.x.y.z:8765/mcp", "headers": {"Authorization": "Bearer <KEY>"}}}}`
- **Claude Desktop** — cầu nối stdio chạy trên máy B (cần cài `tqd-voicestudio` ở máy B):
  `command` = đường dẫn `tqd-voicestudio`, `args` = `["mcp"]`, `env` = `VIZIPVOICE_API_URL=http://100.x.y.z:8765`
  và `VIZIPVOICE_API_KEY=<KEY>`.
- **Web UI** (khi có ở bản sau): mở `http://100.x.y.z:8765/`, nhập API key vào ô key.

Không ghi key vào file nằm trong repo. Kết quả của `synthesize_speech` qua HTTP là âm thanh trả thẳng trong
phản hồi MCP (tối đa 8 MB); lớn hơn thì nhận `download_path` (`/v1/history/<id>/audio`, cần Bearer). Với
`create_voice` qua HTTP hãy gửi `audio_base64` + `filename` và `consent=true`.

## 5. Tailscale HTTPS (tùy chọn)

Để dùng `https://` với chứng chỉ hợp lệ trong tailnet, giữ server ở `127.0.0.1` và để Tailscale làm proxy:

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8765
# client dùng https://<tên-máy>.<tailnet>.ts.net/mcp
```

Khi đó không cần mở cổng 8765 trên tường lửa và có thể giữ `host = "127.0.0.1"`; key vẫn nên được đặt.

## 6. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `401 invalid or missing bearer token` | Sai key hoặc thiếu header | `tqd-voicestudio key show` ở máy A, kiểm tra `Authorization: Bearer ...` |
| Server không khởi động, mã thoát 2 | `host` ngoài loopback nhưng chưa có key | `tqd-voicestudio key new`, rồi khởi động lại |
| `curl` treo / timeout | Firewall Windows chặn hoặc sai profile | Tạo lại rule ở mục 2 (`-Profile Any`), kiểm tra `Get-NetFirewallRule` |
| Không ping được IP `100.x` | Tailscale offline ở một trong hai máy | `tailscale status`, đăng nhập lại |
| `Connection refused` | Server chưa chạy hoặc đang bind `127.0.0.1` | Kiểm tra `host` trong `config.toml`, `tqd-voicestudio stop` rồi chạy lại |
| Cổng 8765 bận | Chương trình khác dùng cổng | Đổi `port` trong `config.toml` (và sửa rule firewall) |
| `/mcp` trả 503 | Server đang khởi động | Đợi `GET /health` ra `status: ok` rồi thử lại |
| Claude Desktop không thấy tool | Chưa dùng cầu nối stdio | Dùng cấu hình số 3 của `mcp-config --remote` |
