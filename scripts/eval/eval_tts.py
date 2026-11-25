#!/usr/bin/env python3
"""Fixed A/B evaluation for ViZipVoice checkpoints.

Run (one model per call, same testset + seed for every model):

  python scripts/eval/eval_tts.py run --model-dir D:/TTS/models/ViZipvoice \
      --checkpoint-name checkpoint-1860000.pt --testset testset.json --out-dir out/base
  python scripts/eval/eval_tts.py compare out/base/summary.json out/ft/summary.json

Testset = prompts (studio + custom + unseen-speaker clips) x FIXED_SENTENCES.
Metrics per utterance:
  cer  - PhoWhisper ASR of the output vs the target text, both normalised with
         normalize_vietnamese_text + normalize_for_compare (intelligibility).
  sim  - cosine similarity of WavLM x-vectors (microsoft/wavlm-base-plus-sv)
         between prompt and output at 16 kHz (speaker similarity).
  fail - cer > FAIL_CER.

Decision rule (compare): adopt the candidate only if ALL hold
  mean_cer(cand)  <= mean_cer(base) + 0.005
  fail_rate(cand) <= fail_rate(base) + 0.01
  mean_sim(cand)  >= mean_sim(base) - 0.01
and at least one real improvement:
  mean_cer(cand) <= mean_cer(base) - 0.005  or  mean_sim(cand) >= mean_sim(base) + 0.01
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

FAIL_CER = 0.2
SEED = 666
# Spelling variants ASR and soe-vinorm disagree on; mapped before CER so they don't count as errors.
EQUIV_WORDS = {"ngàn": "nghìn", "linh": "lẻ", "mốt": "một", "tư": "bốn", "lăm": "năm"}


def _canon(text: str) -> str:
    return " ".join(EQUIV_WORDS.get(w, w) for w in text.split())

FIXED_SENTENCES = [
    "Xin chào.",
    "Cảm ơn bạn rất nhiều.",
    "Hôm nay trời đẹp quá!",
    "Bạn đã ăn cơm chưa?",
    "Buổi sáng, tôi thường đi bộ quanh hồ rồi ghé quán quen uống một ly cà phê sữa đá.",
    "Theo báo cáo mới nhất, doanh thu quý ba năm 2025 đạt 1.250 tỷ đồng, tăng 12,5% so với cùng kỳ.",
    "Cuộc họp sẽ bắt đầu lúc 14 giờ 30 phút ngày 15/08/2026 tại tầng 3, số 25 Lý Thường Kiệt.",
    "Giá vàng hôm nay dao động quanh mức 85 triệu đồng mỗi lượng.",
    "Ứng dụng này hỗ trợ đăng nhập bằng email, Facebook và Google.",
    "Người xưa có câu: có công mài sắt, có ngày nên kim.",
    "Trong Đông y, gừng có vị cay, tính ấm, giúp làm ấm bụng và giảm cảm lạnh.",
    "Nếu bạn cảm thấy mệt mỏi kéo dài, hãy nghỉ ngơi hợp lý và đi khám bác sĩ sớm.",
    "Chiếc xe lao vút qua con đường đất đỏ, để lại phía sau một màn bụi mờ mịt.",
    "Em đứng lặng bên cửa sổ, nhìn mưa rơi tí tách trên những tán lá bàng.",
    "Vì sao bầu trời lại có màu xanh vào ban ngày và màu cam lúc hoàng hôn?",
    "Thị trường chứng khoán phiên chiều nay giảm nhẹ, chỉ số VN-Index mất gần 8 điểm.",
    "Học sinh cần chuẩn bị bút, thước kẻ, máy tính cầm tay và giấy nháp trước giờ thi.",
    "Đường dây nóng hỗ trợ khách hàng hoạt động hai mươi bốn giờ mỗi ngày, kể cả ngày lễ.",
    "Anh ấy nói rất nhỏ, nhưng từng chữ đều rõ ràng và đầy quyết tâm.",
    "Sau nhiều năm xa quê, bà vẫn nhớ như in mùi khói bếp chiều cuối năm.",
    "Phở, bún chả và bánh mì là những món ăn được du khách nước ngoài yêu thích nhất.",
    "Hệ thống sẽ tự động sao lưu dữ liệu vào lúc nửa đêm và gửi thông báo qua tin nhắn.",
    "Đừng bao giờ từ bỏ ước mơ, chỉ vì con đường phía trước còn nhiều chông gai.",
    "Kính chúc quý vị và các bạn một năm mới an khang, thịnh vượng, vạn sự như ý.",
]

STUDIO_PROMPTS = ["Đinh-Quyết", "Nhã-Uyên", "MC", "Thanh-Vân", "Quang-Minh", "Phương-Ngọc"]
CUSTOM_PROMPTS = [
    "vi-bac-nam-ducanh-tunhien-v1",
    "vi-bac-nu-hoaian-tunhien-v1",
    "vi-nam-nam-baolong-tunhien-v1",
    "vi-nam-nu-haiyen-tunhien-v1",
]


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8").strip()


def build_testset(args) -> None:
    prompts = []
    studio_dir = Path(args.studio_dir)
    for name in STUDIO_PROMPTS:
        wav = studio_dir / f"{name}.wav"
        if not wav.is_file():
            wav = studio_dir / f"{name}.mp3"
        prompts.append({"group": "studio", "name": name, "wav": str(wav),
                        "text": _read_text(studio_dir / f"{name}.txt")})
    custom_dir = Path(args.custom_dir)
    for name in CUSTOM_PROMPTS:
        prompts.append({"group": "custom", "name": name, "wav": str(custom_dir / f"{name}.wav"),
                        "text": _read_text(custom_dir / f"{name}.txt")})
    # Unseen speakers: dev-split speakers of each prepared dataset (never trained on).
    for ds_dir in args.unseen_dataset_dir or []:
        ds_dir = Path(ds_dir)
        stats = json.loads((ds_dir / "stats.json").read_text(encoding="utf-8"))
        dev_speakers = list(stats.get("dev_speakers") or [])
        best = {}
        with open(ds_dir / "manifest.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if not r.get("kept") or r.get("speaker") not in dev_speakers:
                    continue
                if not (5.0 <= float(r["duration"]) <= 10.0) or (r.get("cer") or 0) > 0.02:
                    continue
                cur = best.get(r["speaker"])
                if cur is None or float(r["snr"]) > float(cur["snr"]):
                    best[r["speaker"]] = r
        for spk in sorted(best)[: args.unseen_per_dataset]:
            r = best[spk]
            prompts.append({"group": "unseen", "name": f"{ds_dir.name}:{spk}",
                            "wav": r["wav"], "text": r["text"]})
    out = {"seed": SEED, "fail_cer": FAIL_CER, "prompts": prompts, "sentences": FIXED_SENTENCES}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {g: sum(p["group"] == g for p in prompts) for g in ("studio", "custom", "unseen")}
    print(f"testset: {counts} prompts x {len(FIXED_SENTENCES)} sentences -> {args.out}")


def _load_16k(path: str) -> np.ndarray:
    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    wav = torch.from_numpy(wav.mean(axis=1)).unsqueeze(0)
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    return wav.squeeze(0).numpy()


class SpeakerSim:
    def __init__(self, device: str) -> None:
        from transformers import AutoFeatureExtractor, WavLMForXVector

        mid = "microsoft/wavlm-base-plus-sv"
        self.fe = AutoFeatureExtractor.from_pretrained(mid)
        self.model = WavLMForXVector.from_pretrained(mid).to(device).eval()
        self.device = device

    @torch.inference_mode()
    def embed(self, wav16: np.ndarray) -> torch.Tensor:
        inputs = self.fe(wav16, sampling_rate=16000, return_tensors="pt").to(self.device)
        emb = self.model(**inputs).embeddings[0]
        return torch.nn.functional.normalize(emb, dim=-1).cpu()


def run_eval(args) -> None:
    from vizipvoice_addons import asr
    from zipvoice.vizipvoice import ViZipVoiceTTS, normalize_vietnamese_text

    testset = json.loads(Path(args.testset).read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    (out_dir / "wavs").mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tts = ViZipVoiceTTS(model_dir=args.model_dir, checkpoint_name=args.checkpoint_name)
    sim_model = SpeakerSim(device)
    norm = lambda t: normalize_vietnamese_text(t, enabled=True)  # noqa: E731

    rows = []
    for pi, prompt in enumerate(testset["prompts"]):
        prompt_emb = sim_model.embed(_load_16k(prompt["wav"]))
        for si, sentence in enumerate(testset["sentences"]):
            out_wav = out_dir / "wavs" / f"p{pi:02d}_s{si:02d}.wav"
            tts.synthesize(prompt_wav=prompt["wav"], prompt_text=prompt["text"], text=sentence,
                           output_path=out_wav, seed=testset["seed"], num_step=args.num_step)
            wav16 = _load_16k(str(out_wav))
            hyp = asr.transcribe([wav16], 16000)[0]
            c = asr.cer(_canon(asr.normalize_for_compare(sentence, normalizer=norm)),
                        _canon(asr.normalize_for_compare(hyp, normalizer=norm)))
            s = float(torch.dot(prompt_emb, sim_model.embed(wav16)))
            rows.append({"prompt": prompt["name"], "group": prompt["group"], "sentence_idx": si,
                         "cer": round(c, 4), "sim": round(s, 4), "fail": c > testset["fail_cer"],
                         "duration": round(len(wav16) / 16000, 3), "asr": hyp, "wav": str(out_wav)})
        print(f"[eval] prompt {pi + 1}/{len(testset['prompts'])} {prompt['name']} done", flush=True)

    with open(out_dir / "utterances.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def agg(sub):
        return {"n": len(sub), "mean_cer": round(float(np.mean([r["cer"] for r in sub])), 4),
                "median_cer": round(float(np.median([r["cer"] for r in sub])), 4),
                "fail_rate": round(float(np.mean([r["fail"] for r in sub])), 4),
                "mean_sim": round(float(np.mean([r["sim"] for r in sub])), 4)}

    summary = {"model_dir": args.model_dir, "checkpoint": args.checkpoint_name,
               "testset": args.testset, "all": agg(rows)}
    for g in ("studio", "custom", "unseen"):
        sub = [r for r in rows if r["group"] == g]
        if sub:
            summary[g] = agg(sub)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def compare(args) -> int:
    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    for g in ("all", "studio", "custom", "unseen"):
        if g in base and g in cand:
            b, c = base[g], cand[g]
            print(f"{g:7s} cer {b['mean_cer']:.4f} -> {c['mean_cer']:.4f} | "
                  f"fail {b['fail_rate']:.4f} -> {c['fail_rate']:.4f} | "
                  f"sim {b['mean_sim']:.4f} -> {c['mean_sim']:.4f}")
    b, c = base["all"], cand["all"]
    no_regression = (c["mean_cer"] <= b["mean_cer"] + 0.005
                     and c["fail_rate"] <= b["fail_rate"] + 0.01
                     and c["mean_sim"] >= b["mean_sim"] - 0.01)
    improved = c["mean_cer"] <= b["mean_cer"] - 0.005 or c["mean_sim"] >= b["mean_sim"] + 0.01
    verdict = "ADOPT" if (no_regression and improved) else "KEEP_BASE"
    print(f"no_regression={no_regression} improved={improved} -> {verdict}")
    return 0 if verdict == "ADOPT" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-testset")
    b.add_argument("--studio-dir", default="D:/TTS/models/ViZipvoice/audio")
    b.add_argument("--custom-dir", default="D:/TTS/voices")
    b.add_argument("--unseen-dataset-dir", action="append")
    b.add_argument("--unseen-per-dataset", type=int, default=3)
    b.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--model-dir", required=True)
    r.add_argument("--checkpoint-name", default="latest")
    r.add_argument("--testset", required=True)
    r.add_argument("--out-dir", required=True)
    r.add_argument("--num-step", type=int, default=16)
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("candidate")
    args = ap.parse_args()
    if args.cmd == "build-testset":
        build_testset(args)
        return 0
    if args.cmd == "run":
        run_eval(args)
        return 0
    return compare(args)


if __name__ == "__main__":
    sys.exit(main())
