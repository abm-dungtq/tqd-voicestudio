---
phase: 2
title: "Chunker v2 + eval văn bản dài"
status: completed
priority: P1
effort: "4h"
dependencies: [1]
---

> Cwd mặc định: `D:\TTS\tqd-voice` (worktree). `$PY` = `D:/TTS/ViZipvoice/.venv/Scripts/python.exe`. Không sửa file trong `D:\TTS\ViZipvoice`.

# Phase 2: Chunker v2 + eval văn bản dài

## Goal
Thêm bộ gom đoạn 13–25 từ (`TQD_CHUNKER=v2`), đo nó so với cách chia hiện tại (`legacy`) bằng harness văn bản dài với luật quyết định cố định, rồi đặt mặc định theo kết quả.

## Key evidence (lead, đã xác minh)
Eval 384 câu của model gốc theo độ dài câu: 1–4 từ lỗi 50% (CER 0,46); 5–12 từ lỗi 18,8% (CER 0,13); 13–25 từ lỗi 2,8% (CER 0,05). Server hiện tách câu bằng `split_text_into_sentences` (`zipvoice/vizipvoice.py:171`) tại `vizipvoice_openai_server.py:422`, sau đó chỉ gộp mảnh < 6 từ. `_split_long_sentence_clauses` không được gọi ở đâu (code chết, không đụng tới).

## Files to Create / Modify
- Create: `vizipvoice_addons/chunker.py`, `tests/addons/test_chunker.py`, `scripts/eval/eval_longform.py`
- Modify: `vizipvoice_openai_server.py` (hàm `parse_expressive_script`, khối hằng gần dòng 143)
- Input có sẵn (lead soạn, KHÔNG sửa): `plans/261125-1606-tqd-voice/assets/longform_texts.json`

## Tasks

### Task 2.1 — `vizipvoice_addons/chunker.py`
- Goal: hàm thuần, không phụ thuộc torch.
- Steps: viết đúng 3 hàm công khai:
  ```python
  WORD_RE = re.compile(r"\w+", re.UNICODE)

  def weight(text: str) -> int:
      # mỗi token \w+ = 1; token chứa chữ số = min(6, max(1, số ký tự chữ số trong token))

  def split_long(sentence: str, max_words: int = 25) -> list[str]:
      # weight(sentence) <= max_words -> [sentence]
      # Ứng viên điểm cắt: ngay SAU mỗi match của r"[;:,]\s+" (dấu câu ở lại mảnh trái).
      # Chọn ứng viên có weight(trái) gần weight(sentence)/2 nhất mà cả hai bên weight >= 6.
      # Không có ứng viên hợp lệ -> cắt tại ranh giới khoảng trắng gần giữa nhất (theo weight).
      # Đệ quy cho cả hai nửa; strip từng mảnh; bỏ mảnh rỗng.

  def chunk_sentences(sentences: list[str], min_words: int = 13, max_words: int = 25, tail_slack: int = 5) -> list[str]:
      # pieces = [p for s in sentences for p in split_long(s.strip(), max_words) if p.strip()]
      # cur = ""
      # với mỗi p: nếu cur rỗng -> cur = p
      #   elif weight(cur) < min_words và weight(cur)+weight(p) <= max_words -> cur = cur + " " + p
      #   else -> out.append(cur); cur = p
      # cuối: nếu out và weight(cur) < min_words và weight(out[-1])+weight(cur) <= max_words+tail_slack -> out[-1] += " " + cur
      #       ngược lại out.append(cur) (nếu cur không rỗng)
  ```
- Success criteria: không import gì ngoài `re`, `typing`.
- Verify: phủ bởi Task 2.2.

### Task 2.2 — Test chunker
- Target: `tests/addons/test_chunker.py`.
- Steps: viết đúng các test:
  1. `weight("Giá 1290000 đồng") == 8`; `weight("năm 2026") == 5`; `weight("") == 0`.
  2. `chunk_sentences(["Xin chào.", "Cảm ơn bạn rất nhiều.", "Hôm nay trời đẹp quá!"]) == ["Xin chào. Cảm ơn bạn rất nhiều. Hôm nay trời đẹp quá!"]`.
  3. Câu đúng 25 từ (tạo bằng `" ".join(["từ"]*24) + " cuối."`) → `split_long` trả 1 phần tử.
  4. Câu đầu của văn bản `essay` trong `longform_texts.json` (tách bằng `split_text_into_sentences`) → mọi phần tử của `chunk_sentences([...])` có `weight <= 25`.
  5. Bảo toàn nội dung: với mỗi văn bản trong `longform_texts.json`, tách đoạn theo `\n\s*\n`, mỗi đoạn `split_text_into_sentences` → `chunk_sentences`; `" ".join(chunks).split() == " ".join(sentences).split()`.
  6. Đuôi ngắn gộp ngược: `chunk_sentences([twenty, "Xong."])` với `twenty = " ".join(["một"]*19) + " hai."` trả về 1 phần tử.
  7. Trên toàn bộ 6 văn bản: tỉ lệ chunk có `weight < 13` thấp hơn tỉ lệ câu có `weight < 13` của `split_text_into_sentences` (in hai con số trong message assert).
- Verify: `PYTHONUTF8=1 $PY -m pytest tests/addons/test_chunker.py -q` exit 0.

### Task 2.3 — Tích hợp có cờ
- Target: `vizipvoice_openai_server.py`.
- Steps:
  1. Gần `MAX_STREAMS` (dòng ~143) thêm: `CHUNKER_MODE = os.environ.get("TQD_CHUNKER", "legacy").strip().lower()`; nếu không thuộc `{"legacy", "v2"}` thì `raise ValueError(f"TQD_CHUNKER must be legacy|v2, got {CHUNKER_MODE}")`.
  2. Trong `parse_expressive_script`, ngay sau `sents = split_text_into_sentences(norm_tok)` thêm: `if CHUNKER_MODE == "v2": sents = chunker.chunk_sentences(sents)` (import `from vizipvoice_addons import chunker`).
  3. Thêm `"chunker": CHUNKER_MODE` vào JSON của `/health`.
  Không đổi gì khác (giữ vòng gộp < 6 từ, pause, style).
- Verify: tạo `tests/addons/test_chunker_flag.py` với một test chạy subprocess `[sys.executable, "-c", "import vizipvoice_openai_server as s; print(s.CHUNKER_MODE, len(s.parse_expressive_script('Xin chào. Cảm ơn bạn rất nhiều. Hôm nay trời đẹp quá!')))"]` với `cwd` = thư mục gốc repo (`Path(__file__).resolve().parents[2]`) và env = `os.environ` + `PYTHONPATH`=gốc repo + `PYTHONUTF8=1` + `TQD_CHUNKER=v2` (kỳ vọng stdout dòng cuối `v2 1`) và env `TQD_CHUNKER=bogus` (kỳ vọng returncode != 0). Lệnh: `PYTHONUTF8=1 $PY -m pytest tests/addons/test_chunker_flag.py -q` exit 0.

### Task 2.4 — Harness `scripts/eval/eval_longform.py`
- Goal: đo chất lượng theo văn bản dài, so sánh được giữa hai cách chia (số đoạn khác nhau nhưng cùng phủ một văn bản).
- Steps: CLI argparse với 2 lệnh con:
  - `run --server URL --texts PATH --out-dir DIR --label NAME --repeats 2`:
    1. `GET {server}/health`, ghi `chunker` vào summary; nếu khác `--label` thì thoát mã 2 với thông báo.
    2. Với mỗi `repeat` × mỗi `text` × mỗi `voice` trong JSON: `POST {server}/v1/audio/speech/timed` body `{"input": text, "voice": voice, "response_format": "wav", "sample_rate": 24000, "quality_check": false, "subtitle_format": "none"}`, timeout 600 s. Giải mã `audio_base64` → numpy float32 mono 24 kHz; lưu `DIR/wavs/{text_id}_{voice}_{repeat}.wav`.
    3. Với mỗi `segment` (`start`, `end`, `text`, đơn vị giây): cắt `[start-0.05, end+0.05]` (kẹp trong biên), resample 24000 → 16000 bằng `torchaudio.functional.resample`, gom lại gọi `vizipvoice_addons.asr.transcribe(wavs, 16000)` theo lô 8 (giống `eval_tts.py:173-174`).
    4. Chuẩn hóa giống hệt `scripts/eval/eval_tts.py:164,175-176`: `norm = lambda t: normalize_vietnamese_text(t, enabled=True)`; `ref_norm = _canon(asr.normalize_for_compare(segment_text, normalizer=norm))`; `hyp_norm = _canon(asr.normalize_for_compare(hyp, normalizer=norm))`; `_canon` import từ `eval_tts` (thêm thư mục `scripts/eval` vào `sys.path` rồi `from eval_tts import _canon`), không viết lại. `chars = len(ref_norm)`; `edits = asr.cer(ref_norm, hyp_norm) * chars`; bỏ qua segment có `chars == 0`.
    5. Ghi mỗi segment một dòng vào `DIR/segments.jsonl`: `text_id, voice, repeat, seg_idx, words=weight(ref gốc), chars, cer, asr`.
    6. `DIR/summary.json`: `label, chunker, n_docs, n_segments, mean_segment_words, char_cer = Σedits/Σchars, bad_char_share = Σchars của segment có cer > 0.3 / Σchars, total_audio_s, wall_s`.
  - `compare BASE_SUMMARY CAND_SUMMARY`: in một dòng mỗi chỉ số `base -> cand`, rồi dòng quyết định:
    `ADOPT_V2` nếu `cand.char_cer <= base.char_cer - 0.005` VÀ `cand.bad_char_share <= base.bad_char_share`; ngược lại `KEEP_LEGACY`. Ghi cùng nội dung vào `compare.txt` cạnh CAND_SUMMARY.
  Luật quyết định này do lead chốt; KHÔNG đổi ngưỡng sau khi xem kết quả.
- Verify: `PYTHONUTF8=1 $PY scripts/eval/eval_longform.py --help` exit 0 và in ra `run` và `compare`.

### Task 2.5 — Chạy A/B
- Steps (server dev cổng 8011, KHÔNG đụng cổng 8001):
  1. Biến môi trường chung: `PYTHONUTF8=1 HF_HOME=D:/TTS/.hf-cache VIZIPVOICE_MODEL_DIR=D:/TTS/models/ViZipvoice VIZIPVOICE_VOICES_DIR=D:/TTS/voices PORT=8011`.
  2. Khởi động server với `TQD_CHUNKER=legacy`, chờ `/health` trả `status: ok`; chạy `run --server http://127.0.0.1:8011 --texts plans/261125-1606-tqd-voice/assets/longform_texts.json --out-dir D:/TTS/outputs/chunker_eval/legacy --label legacy --repeats 2`; tắt server.
  3. Lặp lại với `TQD_CHUNKER=v2`, `--out-dir D:/TTS/outputs/chunker_eval/v2 --label v2`.
  4. `compare D:/TTS/outputs/chunker_eval/legacy/summary.json D:/TTS/outputs/chunker_eval/v2/summary.json`.
- Success criteria: cả hai summary có `n_docs == 48`.
- Verify: `cat D:/TTS/outputs/chunker_eval/v2/compare.txt` có đúng một dòng `ADOPT_V2` hoặc `KEEP_LEGACY`.

### Task 2.6 — Đặt mặc định theo quyết định
- Production 8001 không bị ảnh hưởng: `start-api.cmd` đã ghim `TQD_CHUNKER=legacy` (Task 1.7).
- Steps: nếu `ADOPT_V2` → đổi mặc định trong `os.environ.get("TQD_CHUNKER", "legacy")` thành `"v2"`. Nếu `KEEP_LEGACY` → giữ `"legacy"`, KHÔNG chỉnh tham số chunker, ghi số liệu vào báo cáo cuối phase cho người dùng quyết định.
- Verify: `grep -n 'os.environ.get("TQD_CHUNKER"' vizipvoice_openai_server.py` hiện giá trị mặc định khớp `compare.txt`; `PYTHONUTF8=1 $PY -m pytest tests/addons tests/tts_service tests/tqd -q` chỉ fail đúng test đã biết `test_generation_ranges_match_public_api_limits`.
- Commit: "Add length-aware chunker v2 with long-form A/B eval".

## Todo
- [x] Task 2.1 — `vizipvoice_addons/chunker.py`
- [x] Task 2.2 — Test chunker
- [x] Task 2.3 — Tích hợp có cờ
- [x] Task 2.4 — Harness `scripts/eval/eval_longform.py`
- [x] Task 2.5 — Chạy A/B
- [x] Task 2.6 — Đặt mặc định theo quyết định

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
