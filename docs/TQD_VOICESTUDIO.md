# TQD-VoiceStudio

TQD-VoiceStudio là công cụ dòng lệnh đọc văn bản tiếng Việt và clone giọng, xây trên
[ViZipVoice](https://github.com/iamdinhthuan/ViZipvoice) (model `contextboxai/ViZipvoice`,
Apache-2.0). Mọi thứ chạy trên máy bạn: server chỉ nghe `127.0.0.1`.

## Lệnh

| Lệnh | Tham số | Việc làm |
|------|---------|----------|
| `tqd-voicestudio doctor` | `--json`, `--bench` | Phân tích máy (OS, GPU, VRAM, driver, RAM, ổ đĩa, ffmpeg, cổng, model) và đề xuất cấu hình. `--bench` đo tốc độ một câu. Thoát mã 1 nếu có lỗi chặn. |
| `tqd-voicestudio install` | `--no-asr`, `--no-smoke` | Tải model, vocoder, ASR vào `TQD_VOICESTUDIO_HOME`, ghi `config.toml`, chạy thử một câu. |
| `tqd-voicestudio say [TEXT]` | `-f FILE`, `-v VOICE`, `-o OUT`, `--format {wav,mp3,flac,opus}`, `--speed X` | Đọc văn bản (TEXT, `-` cho stdin, hoặc `-f`) ra file. Tự bật server nền nếu cần. |
| `tqd-voicestudio clone NAME AUDIO` | `--text T`, `--gender {male,female}`, `--region {bac,trung,nam}`, `--force` | Tạo giọng từ một đoạn thu. Không có `--text` thì server tự nhận dạng lời. |
| `tqd-voicestudio voices` | `--gender`, `--region`, `--json` | Liệt kê giọng. |
| `tqd-voicestudio serve` | `--port N` | Chạy server ở foreground. |
| `tqd-voicestudio mcp` | | MCP server stdio, dùng chung server nền. |
| `tqd-voicestudio mcp-config` | `--client {claude-desktop,cursor,claude-code}` | In cấu hình MCP cho client. |
| `tqd-voicestudio stop` | | Tắt server nền. |

Server nền nghe `127.0.0.1:8765` (đổi bằng `port` trong `config.toml`). Trên Windows server nền
được tạo qua WMI để không bị client MCP kill khi đóng phiên; dùng `tqd-voicestudio stop` để tắt.

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
config.toml            port, device, fp16, num_step, max_streams, default_voice, chunker
models/ViZipvoice/     checkpoint-1860000.pt, config.json, model.json, tokens.txt, audio/ (30 giọng)
voices/                giọng bạn clone (.wav, .txt, .json)
cache/hf/              vocoder và ASR tải từ Hugging Face
outputs/               file âm thanh (outputs/mcp cho MCP)
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

Mã và model theo Apache-2.0 (xem `NOTICE`). Chỉ clone giọng khi người đó đồng ý.
