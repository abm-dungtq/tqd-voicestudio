# TQD-VoiceStudio

TQD-VoiceStudio là công cụ dòng lệnh đọc văn bản tiếng Việt và clone giọng, xây trên
[ViZipVoice](https://github.com/iamdinhthuan/ViZipvoice) (model `contextboxai/ViZipvoice`,
Apache-2.0). Mọi thứ chạy trên máy bạn: server mặc định chỉ nghe `127.0.0.1` (mở cho máy khác
qua Tailscale thì xem [REMOTE.md](REMOTE.md)).

## Lệnh

| Lệnh | Tham số | Việc làm |
|------|---------|----------|
| `tqd-voicestudio doctor` | `--json`, `--bench` | Phân tích máy (OS, GPU, VRAM, driver, RAM, ổ đĩa, ffmpeg, cổng, model) và đề xuất cấu hình. `--bench` đo tốc độ một câu. Thoát mã 1 nếu có lỗi chặn. |
| `tqd-voicestudio install` | `--no-asr`, `--no-smoke`, `--voices vivos`, `--yes` | Tải model, vocoder, ASR vào `TQD_VOICESTUDIO_HOME`, ghi `config.toml`, chạy thử một câu. `--voices vivos` tải gói 22 giọng (17 VIVOS CC BY-NC-SA 4.0, 5 DNU-ViSpeech CC BY-NC 4.0) và đặt `library_dir`. Lệnh hiện giấy phép và hỏi xác nhận; không có terminal thì cần `--yes`. |
| `tqd-voicestudio say [TEXT]` | `-f FILE`, `-v VOICE`, `-o OUT`, `--format {wav,mp3,flac,opus}`, `--speed X`, `--normalize`, `--trim-silence`, `--music ID`, `--job` | Đọc văn bản (TEXT, `-` cho stdin, hoặc `-f`) ra file. Tự bật server nền nếu cần. `--job` gửi thành job nền và theo dõi tiến độ. |
| `tqd-voicestudio clone NAME AUDIO` | `--text T`, `--gender {male,female}`, `--region {bac,trung,nam}`, `--force`, `--consent` | Tạo giọng từ một đoạn thu. Không có `--text` thì server tự nhận dạng lời. **`--consent` bắt buộc**: xác nhận người có giọng đã đồng ý. |
| `tqd-voicestudio voices` | `--gender`, `--region`, `--json` | Liệt kê giọng. |
| `tqd-voicestudio jobs` | `list` / `show ID` / `cancel ID` | Xem và quản lý job nền. |
| `tqd-voicestudio import NGUỒN` | `-o OUT` | Nhập văn bản từ file `.txt`/`.md`/`.docx`/`.pdf` hoặc URL. `.docx`/`.pdf`/URL cần extra `[import]`. |
| `tqd-voicestudio key` | `new` / `show` / `webhook` | Tạo/in API key (cần khi mở server cho máy khác) và secret ký webhook. |
| `tqd-voicestudio serve` | `--port N`, `--host H` | Chạy server ở foreground. Host khác loopback mà chưa có key thì server từ chối khởi động. |
| `tqd-voicestudio mcp` | | MCP server stdio, dùng chung server nền. |
| `tqd-voicestudio mcp-config` | `--client {claude-desktop,cursor,claude-code}`, `--remote URL`, `--key-env NAME`, `--show-key` | In cấu hình MCP cho client; `--remote` in cấu hình HTTP tới server từ xa (xem [REMOTE.md](REMOTE.md)). |
| `tqd-voicestudio stop` | | Tắt server nền. |

Server nền nghe `127.0.0.1:8765` (đổi bằng `port` trong `config.toml`). Trên Windows server nền
được tạo qua WMI để không bị client MCP kill khi đóng phiên; dùng `tqd-voicestudio stop` để tắt.

## Web UI

`GET /` (ví dụ `http://127.0.0.1:8765`) là giao diện web một trang, 6 tab:

- **Đọc** — nhập/dán văn bản, chọn giọng, tốc độ, normalize/trim/nhạc nền, nghe và tải kết quả.
- **Clone giọng** — tải clip lên, điền giới tính/vùng, tick xác nhận đồng ý (tương đương `--consent`).
- **Thư viện giọng** — danh sách giọng preset/custom, xoá giọng custom.
- **Job** — gửi job nền, xem tiến độ theo đoạn, hủy job.
- **Lịch sử** — mọi kết quả đã sinh, nghe/tải lại, chấm điểm từng đoạn, xoá.
- **Cài đặt** — từ điển phát âm (`/v1/lexicon`) và các tuỳ chọn server.

## API server

Mọi endpoint `/v1/*` cần `Authorization: Bearer <key>` khi server có key (trừ `GET /health`).

### `POST /v1/audio/speech` — SpeechRequest

| Trường | Mặc định | Ý nghĩa |
|--------|----------|---------|
| `input` | — | Văn bản (tối đa 25.000 ký tự), hỗ trợ tag `[pause:N]`, `[voice=Tên]` |
| `voice` | `Đinh-Quyết` | Giọng mặc định cho phần không gắn tag |
| `response_format` | `pcm` | `pcm`/`wav`/`mp3`/`opus`/`flac` (định dạng nén cần ffmpeg) |
| `sample_rate` | 24000 | 8000/16000/24000/48000 |
| `speed` | theo giọng | 0.5–2.0 |
| `quality_check` | theo env | Bật QC bằng ASR, header trả `X-QC-Max-CER`/`X-QC-Retries` |
| `cast` | — | Object `{vai: giọng}` cho kịch bản nhiều vai (xem dưới) |
| `normalize` | `false` | Chuẩn hóa âm lượng về −16 LUFS |
| `trim_silence` | `false` | Rút ngắn khoảng lặng dài hơn ~0,6 giây |
| `music` | — | `{"id": "...", "volume_db": -20, "duck_db": -12, "fade_s": 2}` trộn nhạc nền có ducking |
| `cache` | `true` | Dùng cache kết quả (xem mục Cache) |

`POST /v1/audio/speech/timed` nhận thêm `subtitle_format` (`srt`/`vtt`/`both`/`none`) và trả JSON
`audio_base64`, `segments[{start,end,text,voice}]`, `srt`, `vtt` thay vì file âm thanh.

### Cú pháp trong văn bản

- `[pause:N]` — ngắt nghỉ đúng `N` giây (0 < N ≤ 10); chấp nhận `[pause:1,5]` hoặc `[pause:1.5 giây]`.
- `[voice=Tên]` hoặc `[giọng=Tên]` — đổi giọng từ điểm đó trở đi; các đoạn nối nhau bằng 250 ms lặng.
- Kịch bản nhiều vai: mỗi dòng `Tên vai: lời thoại` (tên vai 1–4 từ, không dấu câu). Dòng không có
  tiền tố tiếp tục vai trước đó. Gửi kèm `cast` là object `{tên vai: tên giọng}` — server viết lại
  thành tag `[voice=...]`. Thiếu vai trong cast → 400. `POST /v1/script/roles` với `{"input": ...}`
  trả `{"roles": [...]}` để kiểm tra trước các vai trong kịch bản.

### `POST /v1/jobs` — job nền

Body như SpeechRequest (mặc định `response_format=wav`) cộng thêm `webhook_url` (tuỳ chọn,
phải bắt đầu `http(s)://`) và `subtitles` (mặc định `true`). Trả `202 {"id", "status":"queued"}`.

Vòng đời: `queued` → `running` → `done` / `failed` / `canceled`. Văn bản được chia thành các đoạn
theo ranh giới đổi giọng và đoạn văn (đoạn văn > 400 từ tách tiếp theo câu); từng đoạn render xong
được ghi ra đĩa ngay (`jobs/<id>/seg-NNNN.wav`), nên **khởi động lại server job vẫn chạy tiếp từ
đoạn dở** — job `running` khi server tắt được đưa về `queued` lúc khởi động.

| Endpoint | Việc |
|----------|------|
| `GET /v1/jobs` | Danh sách job |
| `GET /v1/jobs/{id}` | Trạng thái: `progress` (0–1), `segments_done/total`, `error`, `audio_url`, `srt_url`, `vtt_url`, `history_id`, `webhook_attempts` |
| `GET /v1/jobs/{id}/audio` | File âm thanh khi `done` (409 nếu chưa xong) |
| `GET /v1/jobs/{id}/subtitles.srt` / `.vtt` | Phụ đề theo đoạn |
| `POST /v1/jobs/{id}/cancel` | Hủy: job `queued` hủy ngay, job `running` dừng sau đoạn đang chạy |

#### Webhook

Khi job kết thúc (`done`/`failed`/`canceled`), server POST JSON tới `webhook_url`, tối đa 3 lần
với backoff 1/4/16 giây cho tới khi nhận 2xx. Mọi request đều ký HMAC-SHA256 trên thân raw bằng
secret trong config (in ra bằng `tqd-voicestudio key webhook`), header `X-TQD-Signature: sha256=<hex>`.

Kiểm chứng chữ ký phía nhận (Python):

```python
import hashlib, hmac

def verify(secret: str, raw_body: bytes, header: str) -> bool:
    expect = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, header or "")
```

Payload có dạng `{"event": "job.done" | "job.failed" | "job.canceled", "job": {...}}` — phần `job`
giống hệt response của `GET /v1/jobs/{id}` (gồm `status`, `error`, `history_id`, `audio_url`...).
Danh sách các lần gửi webhook nằm trong `webhook_attempts` của job.

### `GET /v1/history` — lịch sử

Mọi kết quả sinh ra (speech thường, timed, job) đều được lưu vào lịch sử. `GET /v1/history?limit=&offset=`
(limit tối đa 500) trả danh sách mới nhất trước; `GET /v1/history/{id}/audio` tải lại file;
`DELETE /v1/history/{id}` xoá. Header `X-TQD-History-Id` của response speech trỏ tới bản ghi tương ứng.

### `POST /v1/ratings` — chấm điểm

Body: `history_id` **hoặc** `job_id`, tuỳ chọn `segment_index` (chấm riêng một đoạn), `rating`
(`1` hoặc `-1`), `note`. Server tự lưu kèm text/voice/model của đoạn để sau này phân tích.
`GET /v1/ratings` trả JSON; `GET /v1/ratings?format=jsonl` trả NDJSON để xuất dữ liệu.

### `/v1/music` — nhạc nền

- `POST /v1/music` (multipart, field `file`, tuỳ chọn `name`) — tải file nhạc lên (tối đa 50 MB),
  trả `{"id", "name", "duration"}`.
- `GET /v1/music` — danh sách; `DELETE /v1/music/{id}` — xoá.
- Dùng trong speech qua trường `music`: `{"id": ..., "volume_db": -20, "duck_db": -12, "fade_s": 2}`.
  Nhạc được lặp/cắt cho khớp độ dài lời, giảm thêm `duck_db` khi có lời (ducking), fade-out `fade_s`
  giây cuối. Đoạn `[pause:N]` được bảo vệ — không bị trim_silence rút ngắn.

### `POST /v1/import` — nhập văn bản

Multipart field `file` (`.txt` `.md` `.docx` `.pdf`) hoặc JSON `{"url": "https://..."}` (tối đa
5 MB). Trả `{"title", "text", "words", ...}` — text đã làm sạch markdown/HTML, sẵn sàng đưa vào
`say`/speech. `.docx` cần `python-docx`, `.pdf` cần `pypdf`, URL cần `beautifulsoup4` — thiếu thì
501 kèm lệnh cài extra `[import]`.

### Giọng

- `GET /v1/voices?gender=&region=&kind=&q=` — lọc theo giới tính/vùng/loại/từ khoá.
- `POST /v1/voices` — clone giọng mới; form field **`consent` bắt buộc `true`** (không có → 400),
  kèm `gender`, `region`, `prompt_text` hoặc để server tự nhận dạng.
- `DELETE /v1/voices/{name}` — xoá giọng **custom**; giọng preset trong model không xoá được (403),
  tên sai → 404.

### Cache

Speech/timed có cache trong SQLite theo khoá gồm input, giọng, tham số sinh, format và **hash của
từ điển phát âm** — đổi `/v1/lexicon` tự động vô hiệu cache cũ. Trùng khoá thì trả lại file đã lưu
với header `X-TQD-Cache: hit`, còn không thì `miss`. Đặt `"cache": false` để bỏ qua. Job không dùng
cache tổng thể nhưng từng đoạn render xong được giữ trên đĩa như đã nói ở mục job.

## Biến môi trường

| Biến | Ý nghĩa |
|------|---------|
| `TQD_VOICESTUDIO_HOME` | Thư mục dữ liệu (mặc định `~/.tqd-voicestudio`) |
| `TQD_CHUNKER` | `legacy` (mặc định) hoặc `v2`, cách chia đoạn văn bản |
| `VIZIPVOICE_MODEL_DIR`, `VIZIPVOICE_VOICES_DIR`, `VIZIPVOICE_CT2_MODEL`, `HF_HOME` | Ghi đè từng thư mục |
| `VIZIPVOICE_LIBRARY_DIR` | Thư mục thư viện giọng ngoài (tùy chọn) |
| `VIZIPVOICE_DEVICE`, `VIZIPVOICE_FP16`, `VIZIPVOICE_NUM_STEP`, `VIZIPVOICE_MAX_STREAMS` | Thiết bị, fp16, số bước, số luồng (thường lấy từ `config.toml`) |
| `VIZIPVOICE_API_URL`, `VIZIPVOICE_API_KEY`, `VIZIPVOICE_OUTPUT_DIR` | Cho MCP: dùng server có sẵn thay vì server nền |
| `VIZIPVOICE_QUALITY_CHECK`, `VIZIPVOICE_QC_MAX_CER`, `VIZIPVOICE_QC_MAX_RETRIES` | Kiểm tra chất lượng bằng ASR |

## Cấu trúc `~/.tqd-voicestudio`

```text
config.toml            port, host, api_key, device, fp16, num_step, max_streams, default_voice, chunker
models/ViZipvoice/     checkpoint-1860000.pt, config.json, model.json, tokens.txt, audio/ (30 giọng)
voices/                giọng bạn clone (.wav, .txt, .json)
cache/hf/              vocoder và ASR tải từ Hugging Face
outputs/               file âm thanh (outputs/mcp cho MCP)
studio.db              SQLite: job, lịch sử, cache, ratings, nhạc nền
jobs/<id>/             từng đoạn wav của job đang chạy (phục hồi sau khởi động lại)
music/                 file nhạc nền đã tải lên
logs/server.log        log server nền
server.pid, server.env.json
```

## Chia đoạn: legacy hay v2

Bộ chia đoạn v2 gom câu thành đoạn 13–25 từ. Đã đo trên 6 văn bản dài × 4 giọng × 2 lượt
(48 lần đọc), chấm CER theo từng đoạn bằng PhoWhisper (`scripts/eval/eval_longform.py`).
Kết quả so `legacy -> v2`, nguyên văn `compare.txt`:

```text
n_docs: 48 -> 48
n_segments: 112 -> 104
mean_segment_words: 38.286 -> 41.231
char_cer: 0.05252 -> 0.05122
bad_char_share: 0.01694 -> 0.0
total_audio_s: 1245.94 -> 1244.21
wall_s: 732.5 -> 739.3
KEEP_LEGACY
```

Mặc định giữ `legacy` và `TQD_CHUNKER=v2` chỉ bật khi bạn chọn. v2 loại hết các đoạn đọc sai
nặng (`bad_char_share` về 0) nhưng CER trung bình chỉ giảm 0,0013, chưa đạt ngưỡng cố định 0,005
nên không đổi mặc định.

## Văn bản dài

Bài 2.830 từ (năm lần sáu văn bản mẫu) ra 564,7 giây âm thanh liền mạch (giọng mặc định,
RTX 3060). Thời gian chạy 200 giây, đã gồm cả lần khởi động server nguội.

## Phiên bản thư viện

`transformers` giữ ở 4.x (`>=4.50,<5`) và `huggingface_hub` ở `>=0.33,<1.0` vì `soe-vinorm`
(chuẩn hóa số, ngày) yêu cầu `huggingface-hub<1.0`. Windows không có quyền symlink vẫn chạy
được: cache Hugging Face dùng bản sao thay cho symlink (tải tuần tự để tránh lỗi của hub 0.36).

## Giấy phép và giọng nói

Mã của TQD-VoiceStudio theo PolyForm Noncommercial 1.0.0 (chỉ phi thương mại, xem `LICENSE`); mã gốc ZipVoice/ViZipVoice giữ Apache-2.0 (xem `NOTICE`). Chỉ clone giọng khi người đó đồng ý.
