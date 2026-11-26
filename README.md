# TQD-Voice

TQD-Voice là công cụ dòng lệnh đọc văn bản tiếng Việt và clone giọng, chạy hoàn toàn trên máy
bạn, xây trên ViZipVoice (model `contextboxai/ViZipvoice`). Có sẵn 30 giọng, server cục bộ kiểu
OpenAI và MCP cho agent.

**Yêu cầu:** Windows hoặc Linux; GPU NVIDIA từ 6 GB là tốt nhất; không có GPU vẫn chạy được
nhưng chậm; cần khoảng 3 GB ổ đĩa.

## Cài một lệnh

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/abm-dungtq/tqd-voice/v0.1.0/install.ps1 | iex
```

Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/abm-dungtq/tqd-voice/v0.1.0/install.sh | bash
```

Hoặc cài thủ công (cần [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install --python 3.12 --torch-backend=auto "git+https://github.com/abm-dungtq/tqd-voice@v0.1.0"
tqd-voice doctor
tqd-voice install
```

## Dùng

```bash
tqd-voice say "Xin chào các bạn." -o xin-chao.wav
tqd-voice clone giong-cua-toi mau.wav --gender female --region nam
tqd-voice voices --gender female
```

## MCP cho agent

`tqd-voice mcp-config` in sẵn cấu hình cho Claude Desktop, Cursor và Claude Code.

## Sự cố

Chạy `tqd-voice doctor`: lệnh này kiểm tra GPU, driver, PyTorch, ffmpeg, cổng và model, rồi nói
rõ cần sửa gì. Tắt server nền bằng `tqd-voice stop`.

Chỉ clone giọng khi người đó đồng ý.

Chi tiết lệnh, biến môi trường và kết quả đo: [docs/TQD_VOICE.md](docs/TQD_VOICE.md).

---

# ViZipVoice

ViZipVoice là bản fine-tune tiếng Việt của [ZipVoice](https://github.com/k2-fsa/ZipVoice), hướng tới zero-shot text-to-speech và voice cloning tiếng Việt. Người dùng chỉ cần một đoạn prompt audio ngắn kèm transcript của prompt, sau đó nhập nội dung tiếng Việt cần đọc.

- GitHub: https://github.com/iamdinhthuan/ViZipvoice
- Hugging Face model: https://huggingface.co/contextboxai/ViZipvoice
- Hugging Face Space: https://huggingface.co/spaces/dinhthuan/ViZipvoice
- Base project: https://github.com/k2-fsa/ZipVoice
- Base paper: https://arxiv.org/abs/2506.13053

## Model

Model đã được upload tại [contextboxai/ViZipvoice](https://huggingface.co/contextboxai/ViZipvoice). Repo Hugging Face chứa:

- `checkpoint-1860000.pt`: checkpoint mới nhất theo training step, đã tách phần inference và lưu FP16.
- `config.json`: cấu hình model kiêm query file để Hugging Face track download stats.
- `model.json`: cấu hình ZipVoice dùng để dựng model.
- `tokens.txt`: tokenizer ký tự tiếng Việt, `244` token.
- `audio/`: `30` ref audio mẫu, mỗi file audio có một file `.txt` cùng basename chứa transcript.
- `demo/`: một vài output demo được sinh bằng checkpoint mới nhất và ref audio trong `audio/`.
- `README.md`: model card, trỏ ngược về repo GitHub này.
- `vizipvoice.py`: wrapper mẫu cho người muốn tải file từ Hugging Face.

Model sử dụng sample rate `24 kHz` và vocoder mặc định `charactr/vocos-mel-24khz`.
Wrapper mặc định sẽ tự tìm các file `checkpoint-<step>.pt` và load checkpoint có step lớn nhất. Hiện tại checkpoint mới nhất là `checkpoint-1860000.pt`.

## Training data & tokenizer

- Dataset training: khoảng `7000` giờ dữ liệu, gồm khoảng `6500` giờ tiếng Việt và `500` giờ tiếng Anh.
- Tokenizer: `SimpleTokenizer` dạng character-level, tức là tách từng ký tự Unicode trong text thành token.
- Vocab: `tokens.txt` có `244` token, bao gồm ký tự tiếng Việt có dấu, chữ cái, số, dấu câu và token padding `_`.
- Text normalization: không dùng phoneme/G2P cho bản Việt này; transcript prompt và text đầu vào được map theo ký tự. Ký tự ngoài vocab sẽ bị tokenizer bỏ qua.
- Vietnamese normalization: wrapper mặc định dùng `soe-vinorm` để chuẩn hóa số, ngày tháng, đơn vị và viết tắt sang dạng đọc cho TTS. Sau khi normalize, wrapper dọn lại khoảng trắng quanh dấu câu, ví dụ `xin chào , bạn ?` thành `xin chào, bạn?`.

## Cài đặt

```bash
git clone https://github.com/iamdinhthuan/ViZipvoice.git
cd ViZipvoice

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
export PYTHONPATH="$PWD:$PYTHONPATH"
```

Nếu cài `piper_phonemize` bị lỗi, chạy lại với link wheel trong `requirements.txt`:

```bash
pip install piper_phonemize -f https://k2-fsa.github.io/icefall/piper_phonemize.html
```

## Chạy nhanh bằng CLI

Chuẩn bị:

- `prompt.wav`: audio giọng mẫu, nên sạch, ít nhiễu, khoảng `3-10` giây.
- `prompt_text`: transcript đúng với nội dung trong `prompt.wav`.
- `text`: câu tiếng Việt muốn sinh.

```bash
python3 -m zipvoice.bin.infer_vizipvoice \
  --prompt-wav prompt.wav \
  --prompt-text "Xin chào, đây là giọng mẫu của tôi." \
  --text "ViZipVoice có thể tổng hợp giọng nói tiếng Việt từ một đoạn mẫu ngắn." \
  --res-wav-path output.wav
```

Mặc định lệnh trên sẽ tải model từ [contextboxai/ViZipvoice](https://huggingface.co/contextboxai/ViZipvoice) và cache bằng `huggingface_hub`.
Nếu trên Hugging Face có nhiều file `checkpoint-<step>.pt`, CLI sẽ tự load file có step lớn nhất.

## Ref audio trên Hugging Face

Repo Hugging Face có thư mục `audio/` gồm `30` ref audio. Mỗi audio đi kèm transcript cùng tên:

```text
audio/Đinh-Quyết.mp3
audio/Đinh-Quyết.txt
```

Tên file đã được làm sạch, chỉ giữ tên audio/người nói và không giữ prefix `lar_*` hoặc hậu tố `Pro`. Gradio sẽ ưu tiên đọc format sidecar này: audio file và `.txt` cùng basename.

## Audio demo

Các file demo được sinh bằng `checkpoint-1860000.pt` với text dài trong thư mục `demo/` trên Hugging Face. Nếu trình Markdown không render audio player, bấm link nghe trực tiếp bên dưới.

**Đinh-Quyết**

<audio controls src="https://huggingface.co/contextboxai/ViZipvoice/resolve/main/demo/demo_01_%C4%90inh-Quy%E1%BA%BFt.wav"></audio>

[Nghe trực tiếp](https://huggingface.co/contextboxai/ViZipvoice/resolve/main/demo/demo_01_%C4%90inh-Quy%E1%BA%BFt.wav)

**Nhã-Uyên**

<audio controls src="https://huggingface.co/contextboxai/ViZipvoice/resolve/main/demo/demo_02_Nh%C3%A3-Uy%C3%AAn.wav"></audio>

[Nghe trực tiếp](https://huggingface.co/contextboxai/ViZipvoice/resolve/main/demo/demo_02_Nh%C3%A3-Uy%C3%AAn.wav)

**MC**

<audio controls src="https://huggingface.co/contextboxai/ViZipvoice/resolve/main/demo/demo_03_MC.wav"></audio>

[Nghe trực tiếp](https://huggingface.co/contextboxai/ViZipvoice/resolve/main/demo/demo_03_MC.wav)

## Chạy Gradio với ref audio trên Hugging Face

Tải model kèm checkpoint, config, tokenizer, `audio/` và `demo/` về local:

```bash
huggingface-cli download contextboxai/ViZipvoice \
  --local-dir models/ViZipvoice \
  --local-dir-use-symlinks False
```

Chạy giao diện Gradio:

```bash
python3 egs/zipvoice/gradio_app.py --exp-dir models/ViZipvoice
```

Nếu không truyền `--ref-audio-dir`, Gradio sẽ tự tìm `models/ViZipvoice/audio/` trước. Mỗi preset hiển thị đúng basename audio và transcript được đọc từ file `.txt` cùng tên.

Một số tham số hay dùng:

```bash
python3 -m zipvoice.bin.infer_vizipvoice \
  --prompt-wav prompt.wav \
  --prompt-text "Transcript của prompt." \
  --text "Nội dung cần đọc." \
  --res-wav-path output.wav \
  --num-step 16 \
  --guidance-scale 1.0 \
  --speed 1.0 \
  --seed 666
```

CLI mặc định chạy cùng flow với Gradio:

- normalize tiếng Việt bằng `soe-vinorm`;
- tách text dài thành từng câu;
- với câu `1` chữ: dùng tối thiểu `24` step và `speed=0.6`;
- với câu `2-4` chữ: dùng `speed=0.8`;
- sinh từng segment rồi ghép lại bằng silence, crossfade và fade in/out.

Có thể chỉnh postprocessing:

```bash
python3 -m zipvoice.bin.infer_vizipvoice \
  --prompt-wav prompt.wav \
  --prompt-text "Transcript của prompt." \
  --text "Nội dung cần đọc." \
  --res-wav-path output.wav \
  --crossfade-ms 80 \
  --silence-ms 180 \
  --fade-in-ms 20 \
  --fade-out-ms 80
```

Tắt Vietnamese text normalization nếu muốn dùng raw text:

```bash
python3 -m zipvoice.bin.infer_vizipvoice \
  --prompt-wav prompt.wav \
  --prompt-text "Transcript của prompt." \
  --text "Nội dung cần đọc." \
  --res-wav-path output.wav \
  --no-vietnamese-normalize
```

## Dùng bằng Python wrapper

```python
from zipvoice.vizipvoice import ViZipVoiceTTS

tts = ViZipVoiceTTS()

metrics = tts.synthesize(
    prompt_wav="prompt.wav",
    prompt_text="Xin chào, đây là giọng mẫu của tôi.",
    text="Đây là câu tiếng Việt được sinh bởi ViZipVoice.",
    output_path="output.wav",
)

print(metrics)
```

Wrapper mặc định:

- tải model từ `contextboxai/ViZipvoice`;
- chọn checkpoint `checkpoint-<step>.pt` mới nhất theo step;
- dùng `SimpleTokenizer` đúng với `tokens.txt` tiếng Việt;
- normalize `prompt_text` và `text` bằng `soe-vinorm`, rồi sửa khoảng trắng quanh dấu câu;
- tách câu và áp rule text ngắn giống Gradio: `1` chữ dùng `step>=24/speed=0.6`, `2-4` chữ dùng `speed=0.8`;
- postprocess nhiều segment bằng silence, crossfade và fade in/out;
- tự chọn `cuda`, `mps` hoặc `cpu`;
- bật autocast FP16 khi chạy trên CUDA.

## Chạy TTS service async

Service API chỉ làm TTS, không quản lý preset voice. Mỗi job upload một audio clone kèm transcript của audio đó.

Chạy local bằng Docker:

```bash
docker build -t vizipvoice-tts .
docker run --rm -p 8000:8000 \
  -e VIZIPVOICE_MODEL_DIR=/models/ViZipvoice \
  -v "$PWD/models/ViZipvoice:/models/ViZipvoice:ro" \
  vizipvoice-tts
```

Tạo job:

```bash
curl -X POST http://localhost:8000/v1/tts/jobs \
  -F 'reference_audio=@prompt.wav' \
  -F 'request={"text":"Nội dung cần đọc.","reference_text":"Transcript đúng của prompt.wav."}'
```

Poll status:

```bash
curl http://localhost:8000/v1/tts/jobs/<job_id>
```

Download audio:

```bash
curl -L http://localhost:8000/v1/tts/jobs/<job_id>/audio -o output.wav
```

## Deploy Modal

Modal deploy dùng worker GPU NVIDIA L4, cold start mặc định để tránh giữ GPU idle:

- `gpu="L4"`
- `min_containers=0`
- `buffer_containers=0`
- `max_containers=1`
- `scaledown_window=5`

Nếu model đã nằm trong Modal Volume:

```bash
export VIZIPVOICE_MODAL_VOLUME=vizipvoice-models
export VIZIPVOICE_MODAL_VOLUME_MOUNT=/models
export VIZIPVOICE_MODEL_DIR=/models/ViZipvoice
modal deploy modal_app.py
```

Nếu muốn dùng Hugging Face cache/download thay vì volume, bỏ `VIZIPVOICE_MODEL_DIR` và set secret nếu cần token:

```bash
export VIZIPVOICE_MODAL_SECRET=vizipvoice-secrets
modal deploy modal_app.py
```

Các biến deploy hay dùng:

```bash
export VIZIPVOICE_MODAL_GPU=L4
export VIZIPVOICE_MODAL_MAX_CONTAINERS=1
export VIZIPVOICE_MODAL_SCALEDOWN_WINDOW=5
export VIZIPVOICE_MODAL_WORKER_TIMEOUT=900
```

## MCP server cho agent + Tailscale

Repo có `mcp_server.py` — MCP server chạy stdio, gọi `vizipvoice_openai_server.py` qua
HTTP. Agent (Claude Desktop, IDE) dùng các tool `list_voices`, `synthesize_speech`,
`create_voice` và resource `vizipvoice://voices` để tạo voice tiếng Việt.

```bash
pip install -r requirements-mcp.txt
export VIZIPVOICE_API_URL=http://127.0.0.1:8001   # hoặc URL tailnet
export VIZIPVOICE_API_KEY=...                     # khớp key của speech server
python mcp_server.py
```

Hướng dẫn đầy đủ (cấu hình client, `tailscale serve`/ACL, xử lý lỗi):
[docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md).

## Chạy với model local

Nếu đã tải model từ Hugging Face về thư mục riêng:

```bash
huggingface-cli download contextboxai/ViZipvoice \
  --local-dir models/ViZipvoice \
  --local-dir-use-symlinks False

python3 -m zipvoice.bin.infer_vizipvoice \
  --model-dir models/ViZipvoice \
  --prompt-wav prompt.wav \
  --prompt-text "Transcript của prompt." \
  --text "Nội dung cần đọc." \
  --res-wav-path output.wav
```

## API mở rộng (vizipvoice_openai_server.py)

Server OpenAI-compatible giờ có thêm các endpoint/tham số mới (xem `vizipvoice_addons/`):

- `GET /v1/voices?gender=&region=&kind=&q=` — lọc giọng theo giới tính
  (`male`/`female`/`nam`/`nữ`), miền (`bac`/`trung`/`nam`/`Bắc`/…), loại
  (`preset`/`custom`), hoặc tìm chuỗi con trong id/mô tả/alias.
- `POST /v1/voices` — thêm form field `gender`, `region`, `auto_transcribe`
  (mặc định bật). Thứ tự lấy transcript: `prompt_text`/`ref_text` → `ref.txt`
  trong thư viện → ASR (model `VIZIPVOICE_ASR_MODEL`, mặc định
  `vinai/PhoWhisper-small`). Clip dài > 10 s sẽ được cắt tự động
  (`select_prompt_segment`) trước khi transcribe. Không có text + ASR lỗi → 400,
  yêu cầu truyền `prompt_text`. Mỗi voice ghi sidecar `voices/<name>.json`
  (gender/region/transcript_source/duration/median_f0).
- `POST /v1/audio/speech` — `response_format` hỗ trợ thêm `mp3`/`opus`/`flac`
  (cần ffmpeg); `quality_check=true` bật QC bằng ASR: từng span được transcribe
  lại, tính CER với text đã chuẩn hoá, sinh lại bằng seed 667+ nếu CER vượt
  `VIZIPVOICE_QC_MAX_CER` (mặc định 0.15), tối đa `VIZIPVOICE_QC_MAX_RETRIES`
  (mặc định 2) lần; header `X-QC-Max-CER`/`X-QC-Retries`. Đa giọng trong một
  request bằng tag `[voice=Tên]` / `[giọng=Tên]`, các đoạn nối bằng 250 ms lặng.
- `POST /v1/audio/speech/timed` — body như `/v1/audio/speech` + `subtitle_format`
  (`srt`/`vtt`/`both`); trả JSON gồm `audio_base64`, `segments[{start,end,text,voice}]`,
  `srt`, `vtt`.
- `GET`/`PUT /v1/lexicon` — từ điển phát âm JSON `{"entries": {...}}`, thay thế
  nguyên từ khoá, phân biệt hoa thường, tự reload theo mtime. Đường dẫn qua
  `VIZIPVOICE_LEXICON_PATH`; mẫu ở `pronunciation_lexicon.example.json`.

## Quy trình dữ liệu & fine-tune

Cài phụ thuộc: `pip install -r requirements-data.txt` (datasets + pyarrow tương
thích numpy<2).

```bash
# 1) Tải & lọc dataset -> wavs/ + manifest.jsonl + custom_{train,dev}.tsv
python scripts/data/prepare_tts_dataset.py \
  --dataset qhuy242/VNAVC --speaker-col speaker_id \
  --streaming --max-samples 400 --asr-filter \
  --out-dir D:/TTS/datasets/vnavc_sample --license cc-by-4.0

# 2) Chọn clip 5-10 s tốt nhất mỗi speaker -> candidates.csv
python scripts/data/select_voice_candidates.py \
  --manifest D:/TTS/datasets/vnavc_sample/manifest.jsonl \
  --out-dir D:/TTS/datasets/vnavc_sample/candidates --top 10

# 3) Nghe thử, điền cột region/name/approve=1 trong candidates.csv rồi import
python scripts/data/import_voices.py \
  --candidates D:/TTS/datasets/vnavc_sample/candidates/candidates.csv
# ghi vi-{bac|trung|nam}-{nam|nu}-{name}-tunhien-v1.{wav,txt,json} vào D:/TTS/voices
```

Fine-tune (TSV từ bước 1; tokenizer `simple`, không cần espeak):

```bash
cd egs/zipvoice
train_tsv=D:/TTS/datasets/vnavc_sample/custom_train.tsv \
dev_tsv=D:/TTS/datasets/vnavc_sample/custom_dev.tsv \
model_dir=D:/TTS/models/ViZipvoice \
python=../../.venv/Scripts/python \
bash run_finetune_vi.sh   # stage 1-6; stage 4 lấy checkpoint từ model_dir
```

## Gợi ý chất lượng

- Prompt audio nên là một người nói, rõ chữ, không nhạc nền, không vang phòng quá mạnh.
- Transcript của prompt phải khớp audio. Sai transcript thường làm giọng clone kém hoặc đọc sai.
- Với câu rất ngắn, có thể giảm `--speed 0.7` hoặc tăng `--num-step 24`.
- Với text dài, nên để dấu câu rõ ràng để model tách đoạn tự nhiên hơn.
- Model mạnh nhất cho tiếng Việt. Ký tự ngoài vocab có thể bị bỏ qua bởi tokenizer ký tự.

## Đạo đức sử dụng

ViZipVoice có khả năng clone giọng từ prompt audio. Chỉ sử dụng giọng nói khi bạn có quyền hoặc được người nói đồng ý. Không dùng model để giả mạo danh tính, lừa đảo, tạo nội dung gây hại, hoặc phát tán thông tin sai lệch.

## License

Code kế thừa ZipVoice và được phát hành theo Apache License 2.0. Vui lòng xem [LICENSE](LICENSE) và ghi nguồn ZipVoice khi sử dụng lại.

## Citation

Nếu dùng nền tảng ZipVoice trong nghiên cứu hoặc sản phẩm, vui lòng cite paper gốc:

```bibtex
@article{zhu2025zipvoice,
  title={ZipVoice: Fast and High-Quality Zero-Shot Text-to-Speech with Flow Matching},
  author={Zhu, Han and others},
  journal={arXiv preprint arXiv:2506.13053},
  year={2025}
}
```
