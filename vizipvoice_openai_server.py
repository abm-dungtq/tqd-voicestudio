#!/usr/bin/env python3
"""ViZipVoice — OpenAI-compatible Vietnamese Speech API Server (CUDA FP16).

Standalone OpenAI-compatible Speech API server on http://127.0.0.1:8001.
Provides:
  - GET  /health
  - GET  /v1/models
  - GET  /v1/voices          (optional filters: gender, region, kind, q)
  - POST /v1/voices          (Multipart voice enrollment + ref.txt / ASR auto-transcription)
  - POST /v1/audio/speech    (OpenAI Speech API: pcm s16le streaming + wav/mp3/opus/flac,
                             8k/16k/24k/48k Hz, speed control, quality_check, [voice=...] runs)
  - POST /v1/audio/speech/timed  (same body + subtitle_format; JSON with base64 audio,
                             per-span segment times, srt/vtt)
  - GET  /v1/lexicon         (pronunciation lexicon entries)
  - PUT  /v1/lexicon         (replace pronunciation lexicon entries)
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import io
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Literal, Optional, Tuple

# Enable CPU fallback for unsupported MPS ops before any torch work happens.
# Must be set before torch is imported/used on macOS (Apple Silicon).
if sys.platform == "darwin" or os.environ.get("VIZIPVOICE_DEVICE", "").strip().lower() == "mps":
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import soundfile as sf
import torch
import torchaudio
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from starlette.routing import Route
from fastapi.responses import JSONResponse, Response, StreamingResponse
from lhotse.utils import fix_random_seed
from pydantic import BaseModel, Field

import tqd_voicestudio
from tqd_voicestudio import config as tqd_config
from tqd_voicestudio import netsec
from tqd_voicestudio import jobs as tqd_jobs
from tqd_voicestudio import paths
from tqd_voicestudio import store as tqd_store

os.environ.setdefault("HF_HOME", str(paths.hf_cache()))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from zipvoice.vizipvoice import (
    ViZipVoiceTTS,
    append_with_crossfade,
    apply_fade,
    get_sentence_inference_params,
    normalize_vietnamese_text,
    split_text_into_sentences,
)
from zipvoice.utils.infer import (
    add_punctuation,
    batchify_tokens,
    chunk_tokens_punctuation,
    cross_fade_concat,
    load_prompt_wav,
    remove_silence,
    rms_norm,
)
import vizipvoice_addons
from vizipvoice_addons import asr as qc_asr
from vizipvoice_addons import chunker, importers, mixing, pauses, script_roles
from vizipvoice_addons import audio_formats, lexicon, script_voices, subtitles, voice_meta
from vizipvoice_addons.asr import cer as qc_cer
from vizipvoice_addons.asr import normalize_for_compare
from vizipvoice_addons.prompt_prep import select_prompt_segment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [vizipvoice-api] %(message)s",
)
log = logging.getLogger("vizipvoice.api")

MODEL_ID = "vizipvoice-v1"
NATIVE_SR = 24000
DEFAULT_OUTPUT_SR = 48000
SUPPORTED_RATES = (8000, 16000, 24000, 48000)
HEADROOM_CEILING = float(10.0 ** (-1.0 / 20.0))  # -1.0 dBFS (~0.8912509)


def trim_edge_silence_float32(
    wav: torch.Tensor,
    sampling_rate: int = NATIVE_SR,
    keep_silence_ms: int = 80,
    silence_threshold_db: float = -48.0,
) -> torch.Tensor:
    """Pure float32 edge-silence trimmer (avoids pydub int16 quantization & clipping)."""
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)
    total_samples = wav.shape[-1]
    frame_len = max(int(sampling_rate * 0.01), 1)
    if total_samples < frame_len * 2:
        return wav
    num_frames = total_samples // frame_len
    frames = wav[0, : num_frames * frame_len].reshape(num_frames, frame_len)
    rms = torch.sqrt(torch.mean(frames * frames, dim=1) + 1e-12)
    thresh = float(10.0 ** (silence_threshold_db / 20.0))
    active = torch.nonzero(rms > thresh, as_tuple=False).squeeze(-1)
    if active.numel() == 0:
        return wav
    keep_samples = int(sampling_rate * keep_silence_ms / 1000)
    start_sample = max(0, int(active[0].item()) * frame_len - keep_samples)
    end_sample = min(total_samples, (int(active[-1].item()) + 1) * frame_len + keep_samples)
    return wav[:, start_sample:end_sample]


def finalize_waveform_for_export(
    wav_1d: torch.Tensor,
    orig_sr: int,
    target_sr: int,
    ceiling: float = HEADROOM_CEILING,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Resample in float32, apply single whole-output -1 dBFS ceiling attenuation, and quantize to PCM16."""
    wav_f32 = wav_1d.detach().cpu().float().squeeze()
    pre_resample_peak = float(torch.max(torch.abs(wav_f32)).item()) if wav_f32.numel() else 0.0
    if orig_sr != target_sr:
        wav_f32 = torchaudio.functional.resample(wav_f32.unsqueeze(0), orig_sr, target_sr).squeeze(0)
    post_resample_peak = float(torch.max(torch.abs(wav_f32)).item()) if wav_f32.numel() else 0.0
    gain = min(1.0, ceiling / post_resample_peak) if post_resample_peak > 0.0 else 1.0
    scaled_f32 = (wav_f32 * gain).numpy().astype(np.float32)
    pcm_f64 = np.rint(scaled_f32 * 32767.0)
    saturated_samples = int(np.sum(np.abs(pcm_f64) >= 32767))
    pcm_i16 = np.clip(pcm_f64, -32768, 32767).astype("<i2")
    export_meta = {
        "pre_resample_peak": round(pre_resample_peak, 5),
        "post_resample_peak": round(post_resample_peak, 5),
        "applied_gain": round(float(gain), 5),
        "final_float_peak": round(float(np.max(np.abs(scaled_f32))) if len(scaled_f32) else 0.0, 5),
        "pcm16_saturated_samples": saturated_samples,
    }
    return scaled_f32, pcm_i16, export_meta

MODEL_DIR = paths.model_dir()
STUDIO_AUDIO_DIR = MODEL_DIR / "audio"
META_JSON_PATH = Path(vizipvoice_addons.__file__).resolve().parent / "data" / "studio_voices_meta.json"
LIBRARY_DIR = (
    Path(os.environ["VIZIPVOICE_LIBRARY_DIR"])
    if os.environ.get("VIZIPVOICE_LIBRARY_DIR")
    else None
)
CUSTOM_VOICES_DIR = paths.voices_dir()

MAX_STREAMS = int(os.environ.get("VIZIPVOICE_MAX_STREAMS", "4"))
_dev = os.environ.get("VIZIPVOICE_DEVICE", "auto").strip().lower()
if _dev == "auto":
    if torch.cuda.is_available():
        DEVICE = "cuda"
    elif torch.backends.mps.is_available():
        DEVICE = "mps"
    else:
        DEVICE = "cpu"
else:
    DEVICE = _dev
if DEVICE == "mps":
    # Fallback for ops MPS does not implement; fp16 autocast stays off on MPS.
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
USE_FP16 = DEVICE == "cuda" and os.environ.get("VIZIPVOICE_FP16", "1").strip() not in ("0", "false", "no")
NUM_STEP = int(os.environ.get("VIZIPVOICE_NUM_STEP", "16"))
CHUNKER_MODE = os.environ.get("TQD_CHUNKER", "legacy").strip().lower()
if CHUNKER_MODE not in {"legacy", "v2"}:
    raise ValueError(f"TQD_CHUNKER must be legacy|v2, got {CHUNKER_MODE}")
QUEUE_TIMEOUT = float(os.environ.get("VIZIPVOICE_QUEUE_TIMEOUT", "180.0"))
API_KEY = os.environ.get("VIZIPVOICE_API_KEY", "").strip()
PCM_CHUNK_SAMPLES = 4800  # 100 ms @ 48 kHz

ACRONYM_MAP = {
    r"\bVN-Index\b": "Vi En In Đếch",
    r"\bVN Index\b": "Vi En In Đếch",
    r"\bVNINDEX\b": "Vi En In Đếch",
    r"\bVN30\b": "Vi En Ba Mươi",
    r"\bFED\b": "Phét",
    r"\bFed\b": "Phét",
    r"\bGDP\b": "Gi Đi Pi",
    r"\bCPI\b": "Xi Pi Ai",
    r"\bFDI\b": "Ép Đi Ai",
    r"\bODA\b": "Ô Đê A",
    r"\bUSD\b": "U Ét Đê",
    r"\bETF\b": "I Ti Ép",
    r"\bP/E\b": "Pi trên I",
    r"\bROE\b": "Rờ Ô E",
    r"\bROA\b": "Rờ Ô A",
}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s.strip())


_DECIMAL_TENS_PATTERN = re.compile(
    r"\bphẩy\s+(hai|ba|bốn|năm|sáu|bảy|tám|chín)\s+(năm|một|bốn)\b",
    flags=re.IGNORECASE,
)
_DECIMAL_UNIT_MAP = {"năm": "lăm", "một": "mốt", "bốn": "tư"}


def _fix_vietnamese_decimals(text: str) -> str:
    text = re.sub(r"\bphẩy\s+một\s+năm\b", "phẩy mười lăm", text, flags=re.IGNORECASE)
    return _DECIMAL_TENS_PATTERN.sub(
        lambda m: f"phẩy {m.group(1)} mươi {_DECIMAL_UNIT_MAP[m.group(2).lower()]}",
        text,
    )


def preprocess_youtube_vietnamese(text: str) -> str:
    """Normalize Vietnamese text and expand finance/macro acronyms for clean TTS reading."""
    out = nfc(text).replace("...", "…").replace("—", ", ").replace("–", ", ")
    out = lexicon.apply(out)
    for pattern, replacement in ACRONYM_MAP.items():
        out = re.sub(pattern, replacement, out)
    normed = normalize_vietnamese_text(out, enabled=True)
    return _fix_vietnamese_decimals(normed)


@dataclass(frozen=True)
class StyleSpec:
    key: str
    pitch_steps: float
    speed_mult: float
    guidance_scale: float
    gain_db: float
    pause_before_ms: int
    pause_after_ms: int


STYLE_SPECS: Dict[str, StyleSpec] = {
    "ke": StyleSpec("ke", pitch_steps=0.0, speed_mult=1.00, guidance_scale=1.00, gain_db=0.0, pause_before_ms=0, pause_after_ms=200),
    "tram": StyleSpec("tram", pitch_steps=0.0, speed_mult=0.96, guidance_scale=1.00, gain_db=0.0, pause_before_ms=160, pause_after_ms=330),
    "nhan": StyleSpec("nhan", pitch_steps=0.0, speed_mult=0.95, guidance_scale=1.00, gain_db=0.0, pause_before_ms=220, pause_after_ms=350),
    "hoi": StyleSpec("hoi", pitch_steps=0.0, speed_mult=0.97, guidance_scale=1.00, gain_db=0.0, pause_before_ms=120, pause_after_ms=420),
    "nhanh": StyleSpec("nhanh", pitch_steps=0.0, speed_mult=1.04, guidance_scale=1.00, gain_db=0.0, pause_before_ms=0, pause_after_ms=155),
}

STYLE_ALIASES: Dict[str, str] = {
    "auto": "auto",
    "ke": "ke",
    "kể": "ke",
    "ke_chuyen": "ke",
    "kể_chuyện": "ke",
    "story": "ke",
    "neutral": "ke",
    "mac_dinh": "ke",
    "mặc_định": "ke",
    "tram": "tram",
    "trầm": "tram",
    "tram_lang": "tram",
    "trầm_lắng": "tram",
    "chiem_nghiem": "tram",
    "chiêm_nghiệm": "tram",
    "calm": "tram",
    "deep": "tram",
    "nhan": "nhan",
    "nhấn": "nhan",
    "nhan_manh": "nhan",
    "nhấn_mạnh": "nhan",
    "cao_trao": "nhan",
    "cao_trào": "nhan",
    "ket_luan": "nhan",
    "kết_luận": "nhan",
    "emphasis": "nhan",
    "dramatic": "nhan",
    "hoi": "hoi",
    "hỏi": "hoi",
    "nghi_van": "hoi",
    "nghi_vấn": "hoi",
    "question": "hoi",
    "nhanh": "nhanh",
    "fast": "nhanh",
    "brisk": "nhanh",
    "tin_tuc": "nhanh",
    "tin_tức": "nhanh",
}


def normalize_style_name(style: Optional[str]) -> str:
    if not style:
        return "auto"
    s = nfc(style).lower().replace("-", "_").replace(" ", "_")
    return STYLE_ALIASES.get(s, "auto")


@dataclass
class PromptVariant:
    style: str
    prompt_features: torch.Tensor
    prompt_rms: float
    prompt_duration: float
    prompt_tokens_str: List[str]
    prompt_tokens: List[List[int]]


@dataclass
class ProsodyClause:
    text: str
    style: str
    speed_mult: float
    guidance_scale: float
    gain_db: float
    pause_before_ms: int
    pause_after_ms: int


_INLINE_TAG_RE = re.compile(
    r"^\[\s*(trầm|tram|calm|deep|nhấn|nhan|emphasis|cao_trao|cao_trào|kể|ke|story|neutral|hỏi|hoi|question|nhanh|fast|tin_tuc|tin_tức|(?:ngắt|ngat|pause)(?:\s*=\s*\d+)?)\s*\]$",
    flags=re.IGNORECASE,
)
_INLINE_SPLIT_RE = re.compile(
    r"(\[\s*(?:trầm|tram|calm|deep|nhấn|nhan|emphasis|cao_trao|cao_trào|kể|ke|story|neutral|hỏi|hoi|question|nhanh|fast|tin_tuc|tin_tức|(?:ngắt|ngat|pause)(?:\s*=\s*\d+)?)\s*\])",
    flags=re.IGNORECASE,
)
_CLAUSE_CONJ_SPLIT_RE = re.compile(
    r"(?:;\s+|,\s+(?=còn\s|trong khi\s|nhưng\s|song\s|tuy nhiên\s|nhờ đó\s|giúp\s|khiến\s|trái lại\s))",
    flags=re.IGNORECASE | re.UNICODE,
)
_EMPHASIS_MARKERS = (
    "quy luật cốt lõi",
    "phép trị cốt yếu",
    "đó chính là",
    "bí quyết",
    "quan trọng nhất",
    "điểm mấu chốt",
    "chìa khóa",
    "tuy nhiên",
    "nhưng thực tế",
    "ngược lại",
    "trái lại",
    "đặc biệt",
    "tóm lại",
    "kết luận lại",
)
_CONTEMPLATIVE_MARKERS = (
    "trong đông y học cổ truyền",
    "người xưa có câu",
    "cổ nhân từng nói",
    "ngẫm lại",
)
_QUESTION_MARKERS = (
    "phải chăng",
    "vì sao",
    "tại sao",
    "như thế nào",
    "bạn có biết",
    "có bao giờ",
)


def _count_words(s: str) -> int:
    return len(re.findall(r"\w+", s, flags=re.UNICODE))


def _split_long_sentence_clauses(sent: str, min_words: int = 6) -> List[Tuple[str, bool]]:
    """Split sentences > 18 words at ';' or major discourse conjunctions only when both sides >= min_words."""
    if _count_words(sent) <= 18:
        return [(sent, False)]
    parts = [p.strip() for p in _CLAUSE_CONJ_SPLIT_RE.split(sent) if p and p.strip()]
    if len(parts) <= 1:
        return [(sent, False)]
    merged: List[str] = []
    for p in parts:
        if merged and _count_words(p) < min_words:
            merged[-1] = f"{merged[-1]}, {p}"
        else:
            merged.append(p)
    if len(merged) > 1 and _count_words(merged[0]) < min_words:
        merged[1] = f"{merged[0]}, {merged[1]}"
        merged.pop(0)
    return [(c, idx < len(merged) - 1) for idx, c in enumerate(merged)]


def _detect_auto_style(raw_clause: str) -> str:
    s_clean = nfc(raw_clause)
    s_low = s_clean.lower()
    if s_clean.endswith("?"):
        return "hoi"
    if s_clean.endswith("!") or any(m in s_low for m in _EMPHASIS_MARKERS):
        return "nhan"
    if s_clean.endswith("...") or s_clean.endswith("…") or any(m in s_low for m in _CONTEMPLATIVE_MARKERS):
        return "tram"
    return "ke"


def _ensure_clause_punctuation(
    text: str, style: str, raw_ended_with: str = "", explicit_style: bool = False
) -> str:
    t = text.strip()
    if not t:
        return t
    if raw_ended_with in ("...", "…"):
        t = re.sub(r"[.!?,;:…]+$", "", t).strip() + "…"
        return t
    if raw_ended_with == "?" or (style == "hoi" and explicit_style):
        t = re.sub(r"[.!?,;:…]+$", "", t).strip() + "?"
        return t
    if raw_ended_with == "!" or (style == "nhan" and explicit_style):
        t = re.sub(r"[.,;:]+$", "", t).strip()
        if not t.endswith(("!", "?")):
            t += "!"
        return t
    if t[-1] not in {";", ":", ",", ".", "!", "?", "…", "；", "：", "，", "。", "！", "？"}:
        t += "."
    return t


def parse_expressive_script(text: str, default_style: Optional[str] = "auto") -> List[ProsodyClause]:
    """Parse inline emotion/pause tags BEFORE soe-vinorm and assign clause-level Vietnamese prosody."""
    base_mode = normalize_style_name(default_style)
    raw_text = nfc(text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r?\n", raw_text) if p.strip()]
    if not paragraphs:
        return []

    raw_items: List[Tuple[str, Optional[str], int, bool, bool]] = []
    # Tuple: (raw_clause_text, explicit_style, extra_pause_before_ms, is_para_start, is_internal_subclause)
    for p_idx, para in enumerate(paragraphs):
        tokens = _INLINE_SPLIT_RE.split(para)
        active_tag_style: Optional[str] = None if base_mode == "auto" else base_mode
        pending_pause_ms: int = 480 if p_idx > 0 else 0
        first_in_para = True

        for tok in tokens:
            tok_s = tok.strip()
            if not tok_s:
                continue
            m_tag = _INLINE_TAG_RE.match(tok_s)
            if m_tag:
                inner = m_tag.group(1).strip().lower()
                if inner.startswith(("ngắt", "ngat", "pause")):
                    if "=" in inner:
                        try:
                            val = int(inner.split("=", 1)[1].strip())
                        except ValueError:
                            val = 380
                    else:
                        val = 380
                    pending_pause_ms = max(80, min(val, 1500))
                else:
                    active_tag_style = normalize_style_name(inner)
                continue

            norm_tok = preprocess_youtube_vietnamese(tok_s)
            sents = split_text_into_sentences(norm_tok)
            if CHUNKER_MODE == "v2":
                sents = chunker.chunk_sentences(sents)
            for s in sents:
                s_clean = s.strip()
                if not s_clean:
                    continue
                raw_items.append(
                    (s_clean, active_tag_style, pending_pause_ms, first_in_para, False)
                )
                pending_pause_ms = 0
                first_in_para = False
    # Merge any accidental < 6 word clause with adjacent clause of same explicit style so word_count >= 6
    merged_items: List[Tuple[str, Optional[str], int, bool, bool]] = []
    for item in raw_items:
        c_txt, c_style, c_pause, c_para, c_int = item
        if merged_items and _count_words(c_txt) < 6 and merged_items[-1][1] == c_style and c_pause == 0:
            prev_txt, prev_style, prev_pause, prev_para, _ = merged_items[-1]
            merged_items[-1] = (f"{prev_txt} {c_txt}", prev_style, prev_pause, prev_para, c_int)
        else:
            merged_items.append(item)
    if len(merged_items) > 1 and _count_words(merged_items[0][0]) < 6 and merged_items[0][1] == merged_items[1][1]:
        first_txt, first_style, first_pause, first_para, _ = merged_items.pop(0)
        sec_txt, sec_style, sec_pause, _, sec_int = merged_items[0]
        merged_items[0] = (f"{first_txt} {sec_txt}", sec_style, first_pause + sec_pause, first_para, sec_int)

    clauses: List[ProsodyClause] = []
    for idx, (c_txt, exp_style, extra_pause, is_para_start, is_internal) in enumerate(merged_items):
        has_explicit = bool(exp_style and exp_style != "auto")
        chosen_style = exp_style if has_explicit else _detect_auto_style(c_txt)
        spec = STYLE_SPECS.get(chosen_style, STYLE_SPECS["ke"])
        raw_tail = "..." if c_txt.endswith("...") else ("…" if c_txt.endswith("…") else c_txt[-1:])
        norm_c = _ensure_clause_punctuation(
            c_txt, chosen_style, raw_ended_with=raw_tail, explicit_style=has_explicit
        )
        if not norm_c.strip():
            continue
        if clauses:
            if extra_pause > 0:
                # Explicit [ngắt=N] or paragraph break sets the exact boundary pause (no stacking)
                clauses[-1].pause_after_ms = min(extra_pause, 1500)
            elif not is_para_start and spec.pause_before_ms > 0:
                clauses[-1].pause_after_ms = max(clauses[-1].pause_after_ms, spec.pause_before_ms)
        pause_after = 220 if is_internal else spec.pause_after_ms
        clauses.append(
            ProsodyClause(
                text=norm_c,
                style=chosen_style,
                speed_mult=spec.speed_mult,
                guidance_scale=spec.guidance_scale,
                gain_db=spec.gain_db,
                pause_before_ms=0,
                pause_after_ms=pause_after,
            )
        )
    return clauses


@dataclass
class VoiceEntry:
    name: str
    wav_path: Path
    prompt_text: str
    kind: Literal["preset", "custom"] = "preset"
    gender: Optional[str] = None
    region: Optional[str] = None
    style: Optional[str] = None
    median_f0_hz: Optional[float] = None
    transcript_source: Optional[str] = None
    featured: bool = False
    recommended_speed: float = 1.0
    description: str = ""
    aliases: List[str] = field(default_factory=list)
    # Cached prompt tensors on GPU
    base_prompt_wav: Optional[torch.Tensor] = None
    prompt_features: Optional[torch.Tensor] = None
    prompt_rms: float = 0.1
    prompt_duration: float = 0.0
    prompt_tokens_str: Optional[List[str]] = None
    prompt_tokens: Optional[List[List[int]]] = None
    style_prompts: Dict[str, PromptVariant] = field(default_factory=dict)


class MusicSpec(BaseModel):
    id: str
    volume_db: float = -20.0
    duck_db: float = -12.0
    fade_s: float = Field(default=2.0, ge=0.0, le=30.0)


class SpeechRequest(BaseModel):
    model: str = Field(default=MODEL_ID)
    input: str = Field(..., min_length=1, max_length=25000)
    voice: str = Field(default="Đinh-Quyết")
    response_format: Literal["pcm", "wav", "mp3", "opus", "flac"] = Field(default="pcm")
    sample_rate: int = Field(default=DEFAULT_OUTPUT_SR)
    speed: Optional[float] = Field(default=None, ge=0.5, le=2.0)
    style: Optional[str] = Field(default="auto")
    quality_check: Optional[bool] = Field(default=None)
    cast: Optional[Dict[str, str]] = Field(default=None)
    normalize: bool = Field(default=False)
    trim_silence: bool = Field(default=False)
    music: Optional[MusicSpec] = Field(default=None)
    cache: bool = Field(default=True)


class JobRequest(SpeechRequest):
    response_format: Literal["pcm", "wav", "mp3", "opus", "flac"] = Field(default="wav")
    webhook_url: Optional[str] = Field(default=None)
    subtitles: bool = Field(default=True)


class RolesBody(BaseModel):
    input: str


class RatingBody(BaseModel):
    history_id: Optional[str] = None
    job_id: Optional[str] = None
    segment_index: Optional[int] = None
    rating: Literal[1, -1]
    note: Optional[str] = None


class TimedSpeechRequest(SpeechRequest):
    subtitle_format: Optional[str] = Field(default="both")  # srt | vtt | both | none


class LexiconBody(BaseModel):
    entries: Dict[str, str] = Field(default_factory=dict)
class ViZipVoiceEngine:
    def __init__(self, max_streams: int = MAX_STREAMS, queue_timeout: float = QUEUE_TIMEOUT) -> None:
        self.max_streams = max(1, int(max_streams))
        self.queue_timeout = queue_timeout
        self._gate = threading.BoundedSemaphore(self.max_streams)
        self._gpu_lock = threading.RLock()
        self._counter_lock = threading.Lock()
        self.active = 0
        self.waiting = 0

        self.voices: Dict[str, VoiceEntry] = {}
        self.alias_map: Dict[str, str] = {}
        self.default_voice = "Đinh-Quyết"

        t0 = time.perf_counter()
        self.tts = ViZipVoiceTTS(
            model_dir=MODEL_DIR,
            checkpoint_name="checkpoint-1860000.pt",
            device=DEVICE,
            use_fp16=USE_FP16,
            num_threads=4,
        )
        self._load_studio_voices()
        self._load_persisted_voices()
        # Pre-warm default voice prompt features + 1 short forward pass
        default_entry = self.resolve_voice(self.default_voice)
        self._ensure_cached_prompt(default_entry)
        _ = self.synthesize_tensor("Âm dương ngũ hành, thuận theo tự nhiên.", default_entry, speed=1.0)
        log.info(
            "ViZipVoiceEngine ready in %.2fs | total voices=%d | aliases=%d | device=%s",
            time.perf_counter() - t0,
            len(self.voices),
            len(self.alias_map),
            self.tts.device,
        )

    def _load_studio_voices(self) -> None:
        meta_raw = {}
        if META_JSON_PATH.is_file():
            meta_raw = json.loads(META_JSON_PATH.read_text(encoding="utf-8"))
        self.default_voice = nfc(meta_raw.get("default_voice", "Đinh-Quyết"))
        voices_meta = meta_raw.get("voices", {})
        aliases_meta = meta_raw.get("aliases", {})

        # Map reverse aliases per canonical voice
        rev_aliases: Dict[str, List[str]] = {}
        for alias_name, target_name in aliases_meta.items():
            a_norm = nfc(alias_name)
            t_norm = nfc(target_name)
            self.alias_map[a_norm.lower()] = t_norm
            rev_aliases.setdefault(t_norm, []).append(a_norm)

        for txt_path in sorted(STUDIO_AUDIO_DIR.glob("*.txt")):
            name = nfc(txt_path.stem)
            wav_path = txt_path.with_suffix(".wav")
            if not wav_path.is_file():
                wav_path = txt_path.with_suffix(".mp3")
            if not wav_path.is_file():
                continue
            prompt_text = nfc(txt_path.read_text(encoding="utf-8"))
            vmeta = voices_meta.get(name, {})
            entry = VoiceEntry(
                name=name,
                wav_path=wav_path,
                prompt_text=prompt_text,
                kind="preset",
                gender=vmeta.get("gender"),
                region=vmeta.get("region"),
                featured=bool(vmeta.get("featured", False)),
                recommended_speed=float(vmeta.get("recommended_speed", 1.0)),
                description=vmeta.get("description", f"Studio voice {name}"),
                aliases=rev_aliases.get(name, []),
            )
            self.voices[name] = entry
            self.alias_map[name.lower()] = name


    def _compute_initial_custom_speed(self, wav_path: Path, prompt_text: str) -> float:
        try:
            info = sf.info(str(wav_path))
            dur_sec = max(float(info.frames) / float(max(info.samplerate, 1)), 0.5)
        except Exception:
            dur_sec = 5.0
        word_cnt = max(len(prompt_text.split()), 1)
        wps = word_cnt / dur_sec
        return round(min(max(3.8 / wps, 0.96), 1.15), 2) if wps < 3.4 else 0.98

    def _load_persisted_voices(self) -> None:
        """Auto-load all 17 VIVOS library voices and custom voices in voices/ on engine startup."""
        # 1. Scan voices/ directory first (24kHz normalized clips enrolled previously)
        if CUSTOM_VOICES_DIR.is_dir():
            for wav_path in sorted(CUSTOM_VOICES_DIR.glob("*.wav")):
                name = nfc(wav_path.stem)
                if not name or "__" in name or name in self.voices:
                    continue
                prompt_text = self._find_library_ref_text(name)
                if not prompt_text:
                    prompt_text = "Xin chào, đây là mẫu giọng nói tiếng Việt tự nhiên."
                is_vivos = LIBRARY_DIR is not None and (LIBRARY_DIR / name).is_dir()
                desc = f"VIVOS library voice ({name})" if is_vivos else f"Enrolled voice ({name})"
                rec_speed = self._compute_initial_custom_speed(wav_path, prompt_text)
                entry = VoiceEntry(
                    name=name,
                    wav_path=wav_path,
                    prompt_text=prompt_text,
                    kind="custom",
                    description=desc,
                    recommended_speed=rec_speed,
                )
                self._apply_voice_metadata(entry)
                self.voices[name] = entry
                self.alias_map[name.lower()] = name

        # 2. Scan voice-share-vivos-17 library for any voice not yet in voices/
        if LIBRARY_DIR is not None and LIBRARY_DIR.is_dir():
            for sub in sorted(LIBRARY_DIR.iterdir()):
                if not sub.is_dir():
                    continue
                name = nfc(sub.name)
                if not name or "__" in name or name in self.voices:
                    continue
                wav_path = sub / "ref.wav"
                if not wav_path.is_file():
                    continue
                prompt_text = self._find_library_ref_text(name)
                if not prompt_text:
                    continue
                rec_speed = self._compute_initial_custom_speed(wav_path, prompt_text)
                entry = VoiceEntry(
                    name=name,
                    wav_path=wav_path,
                    prompt_text=prompt_text,
                    kind="custom",
                    description=f"VIVOS library voice ({name})",
                    recommended_speed=rec_speed,
                )
                self.voices[name] = entry
                self.alias_map[name.lower()] = name
    @staticmethod
    def _apply_voice_metadata(entry: VoiceEntry) -> None:
        """Fill gender/region/description/style/f0 from sidecar JSON or the voice name."""
        sidecar = voice_meta.read_sidecar(CUSTOM_VOICES_DIR / f"{entry.name}.json")
        parsed = voice_meta.parse_voice_name(entry.name) or {}
        entry.gender = sidecar.get("gender") or parsed.get("gender")
        entry.region = sidecar.get("region") or parsed.get("region")
        entry.style = parsed.get("style")
        if sidecar.get("description"):
            entry.description = str(sidecar["description"])
        mf0 = sidecar.get("median_f0_hz")
        if isinstance(mf0, (int, float)):
            entry.median_f0_hz = float(mf0)

    def delete_voice(self, name: str) -> VoiceEntry:
        """Remove a user-enrolled voice (files in CUSTOM_VOICES_DIR). Presets and pack voices are refused."""
        entry = self.resolve_voice(name)
        if entry.kind != "custom":
            raise PermissionError("không xóa được giọng studio có sẵn")
        if LIBRARY_DIR is not None and (LIBRARY_DIR / entry.name).is_dir():
            raise PermissionError("giọng thuộc gói thư viện (chỉ đọc), không xóa được")
        wav = Path(entry.wav_path).resolve()
        if CUSTOM_VOICES_DIR.resolve() not in wav.parents:
            raise PermissionError("giọng thuộc gói thư viện (chỉ đọc), không xóa được")
        for ext in (".wav", ".txt", ".json"):
            (CUSTOM_VOICES_DIR / f"{entry.name}{ext}").unlink(missing_ok=True)
        self.voices.pop(entry.name, None)
        for key in [k for k, v in self.alias_map.items() if v == entry.name]:
            self.alias_map.pop(key, None)
        return entry

    def resolve_voice(self, name: str) -> VoiceEntry:
        q = nfc(name)
        if q in self.voices:
            return self.voices[q]
        mapped = self.alias_map.get(q.lower())
        if mapped and mapped in self.voices:
            return self.voices[mapped]
        raise KeyError(name)

    def list_voices(self) -> List[dict]:
        out = []
        # Featured presets first, then other presets, then custom enrolled voices
        sorted_entries = sorted(
            self.voices.values(),
            key=lambda e: (0 if e.kind == "preset" and e.featured else (1 if e.kind == "preset" else 2), e.name),
        )
        for e in sorted_entries:
            item = {
                "id": e.name,
                "name": e.name,
                "description": e.description,
                "gender": e.gender,
                "region": e.region,
                "featured": e.featured,
                "aliases": e.aliases,
                "kind": e.kind,
                "recommended_speed": e.recommended_speed,
            }
            if e.style:
                item["style"] = e.style
            if e.median_f0_hz is not None:
                item["median_f0_hz"] = e.median_f0_hz
            out.append(item)
        return out

    def _find_library_ref_text(self, name: str) -> str:
        """Auto-discover ref.txt from voice-share-vivos-17 or voices/ if caller didn't pass prompt_text."""
        candidates = [CUSTOM_VOICES_DIR / f"{name}.txt"]
        if LIBRARY_DIR is not None:
            candidates.append(LIBRARY_DIR / name / "ref.txt")
        for c in candidates:
            if c.is_file():
                txt = nfc(c.read_text(encoding="utf-8"))
                if txt:
                    return txt
        return ""

    def enroll_voice(
        self,
        name: str,
        wav_bytes: bytes,
        prompt_text: str = "",
        description: str = "",
        force: bool = False,
        gender: Optional[str] = None,
        region: Optional[str] = None,
        auto_transcribe: bool = True,
    ) -> VoiceEntry:
        name = nfc(name)
        if not name:
            raise ValueError("voice name must not be empty")
        if any(c in name for c in '/\\:*?"<>|') or ".." in name:
            raise ValueError("voice name must not contain path characters")
        if name in self.voices and not force:
            raise FileExistsError(f"voice {name!r} already exists")

        CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        # Decode and save as 24kHz mono wav for fast loading
        data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        tensor = torch.from_numpy(data).unsqueeze(0)
        if sr != NATIVE_SR:
            tensor = torchaudio.functional.resample(tensor, sr, NATIVE_SR)

        # Transcript resolution order: user text -> library ref.txt -> ASR
        resolved_text = ""
        transcript_source = ""
        if prompt_text and prompt_text.strip():
            resolved_text = nfc(prompt_text)
            transcript_source = "user"
        else:
            lib_text = self._find_library_ref_text(name)
            if lib_text:
                resolved_text = lib_text
                transcript_source = "library"

        if not resolved_text:
            if not auto_transcribe or not qc_asr.is_available():
                raise ValueError(
                    "no transcript available: pass prompt_text (or ref_text), "
                    "or enable ASR auto-transcription"
                )
            # Long clip: crop the cleanest 5-10 s speech window first so both
            # the saved prompt and the ASR transcript describe the same segment.
            if tensor.shape[-1] > 10 * NATIVE_SR:
                s0, s1 = select_prompt_segment(
                    tensor.squeeze(0).numpy(), NATIVE_SR
                )
                if s1 > s0:
                    tensor = tensor[:, s0:s1]
            try:
                texts = qc_asr.transcribe(
                    [tensor.squeeze(0).numpy()], NATIVE_SR
                )
            except Exception as exc:
                raise ValueError(
                    f"ASR auto-transcription failed ({exc}); pass prompt_text"
                ) from exc
            resolved_text = nfc(texts[0]) if texts and texts[0].strip() else ""
            if not resolved_text:
                raise ValueError(
                    "ASR produced an empty transcript; pass prompt_text"
                )
            transcript_source = "asr"

        # Trim to 12s max so prompt stays fast
        max_samples = NATIVE_SR * 12
        if tensor.shape[-1] > max_samples:
            tensor = tensor[:, :max_samples]

        saved_wav = CUSTOM_VOICES_DIR / f"{name}.wav"
        torchaudio.save(str(saved_wav), tensor, NATIVE_SR)

        saved_txt = CUSTOM_VOICES_DIR / f"{name}.txt"
        saved_txt.write_text(resolved_text, encoding="utf-8")

        dur_sec = max(float(tensor.shape[-1]) / float(NATIVE_SR), 0.5)
        gender_norm = voice_meta.normalize_gender(gender) or ((gender or "").strip() or None)
        region_norm = voice_meta.normalize_region(region) or ((region or "").strip() or None)
        try:
            median_f0 = voice_meta.estimate_median_f0(tensor, NATIVE_SR)
        except Exception:
            median_f0 = None
        voice_meta.write_sidecar(
            CUSTOM_VOICES_DIR / f"{name}.json",
            {
                "gender": gender_norm,
                "region": region_norm,
                "description": description,
                "transcript_source": transcript_source,
                "duration_s": round(dur_sec, 3),
                "median_f0_hz": round(median_f0, 1) if median_f0 else None,
                "created_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
            },
        )
        word_cnt = max(len(resolved_text.split()), 1)
        wps = word_cnt / dur_sec
        rec_speed = round(min(max(3.8 / wps, 0.96), 1.32), 2) if wps < 3.4 else 0.98

        entry = VoiceEntry(
            name=name,
            wav_path=saved_wav,
            prompt_text=resolved_text,
            kind="custom",
            description=description or f"Enrolled voice ({name})",
            recommended_speed=rec_speed,
            gender=gender_norm,
            region=region_norm,
            style=(voice_meta.parse_voice_name(name) or {}).get("style"),
            median_f0_hz=median_f0,
            transcript_source=transcript_source,
        )
        self._ensure_cached_prompt(entry)
        self.voices[name] = entry
        self.alias_map[name.lower()] = name
        return entry

    @torch.inference_mode()
    def _ensure_cached_prompt(self, entry: VoiceEntry) -> None:
        if entry.prompt_features is not None and entry.prompt_tokens is not None:
            return
        with self._gpu_lock:
            if entry.prompt_features is not None and entry.prompt_tokens is not None:
                return
            pwav = load_prompt_wav(str(entry.wav_path), sampling_rate=NATIVE_SR)
            pwav = remove_silence(pwav, NATIVE_SR, only_edge=False, trail_sil=200)
            pwav, prms = rms_norm(pwav, target_rms=0.1)
            pdur = float(pwav.shape[-1]) / float(NATIVE_SR)
            pfeat = self.tts.feature_extractor.extract(pwav, sampling_rate=NATIVE_SR).to(self.tts.device)
            pfeat = pfeat.unsqueeze(0) * 0.1  # feat_scale=0.1

            norm_ptext = add_punctuation(normalize_vietnamese_text(entry.prompt_text, enabled=True))
            ptok_str = self.tts.tokenizer.texts_to_tokens([norm_ptext])[0]
            ptok_ids = self.tts.tokenizer.tokens_to_token_ids([ptok_str])

            entry.base_prompt_wav = pwav.float().cpu()
            entry.prompt_features = pfeat
            entry.prompt_rms = float(prms)
            entry.prompt_duration = pdur
            entry.prompt_tokens_str = ptok_str
            entry.prompt_tokens = ptok_ids
            base_variant = PromptVariant(
                style="ke",
                prompt_features=pfeat,
                prompt_rms=float(prms),
                prompt_duration=pdur,
                prompt_tokens_str=ptok_str,
                prompt_tokens=ptok_ids,
            )
            entry.style_prompts["ke"] = base_variant
            entry.style_prompts["nhanh"] = base_variant
            if entry.kind == "custom":
                prompt_tps = len(ptok_str) / max(pdur, 0.5)
                if prompt_tps < 14.5:
                    entry.recommended_speed = round(min(max(14.5 / max(prompt_tps, 1.0), 0.96), 1.15), 2)

    @torch.inference_mode()
    def _ensure_cached_prompt_style(self, entry: VoiceEntry, style: str) -> PromptVariant:
        self._ensure_cached_prompt(entry)
        style_key = style if style in STYLE_SPECS else "ke"
        if style_key in entry.style_prompts:
            return entry.style_prompts[style_key]
        with self._gpu_lock:
            if style_key in entry.style_prompts:
                return entry.style_prompts[style_key]
            spec = STYLE_SPECS[style_key]
            # 1. Check if a custom human emotion reference clip <voice>__<style>.wav exists on disk
            custom_emo_wav = CUSTOM_VOICES_DIR / f"{entry.name}__{style_key}.wav"
            custom_emo_txt = CUSTOM_VOICES_DIR / f"{entry.name}__{style_key}.txt"
            if custom_emo_wav.is_file():
                pwav = load_prompt_wav(str(custom_emo_wav), sampling_rate=NATIVE_SR)
                pwav = remove_silence(pwav, NATIVE_SR, only_edge=False, trail_sil=200)
                pwav, prms = rms_norm(pwav, target_rms=0.1)
                pdur = float(pwav.shape[-1]) / float(NATIVE_SR)
                pfeat = self.tts.feature_extractor.extract(pwav, sampling_rate=NATIVE_SR).to(self.tts.device)
                pfeat = pfeat.unsqueeze(0) * 0.1
                emo_text = (
                    nfc(custom_emo_txt.read_text(encoding="utf-8"))
                    if custom_emo_txt.is_file()
                    else entry.prompt_text
                )
                norm_ptext = add_punctuation(normalize_vietnamese_text(emo_text, enabled=True))
                ptok_str = self.tts.tokenizer.texts_to_tokens([norm_ptext])[0]
                ptok_ids = self.tts.tokenizer.tokens_to_token_ids([ptok_str])
                variant = PromptVariant(
                    style=style_key,
                    prompt_features=pfeat,
                    prompt_rms=float(prms),
                    prompt_duration=pdur,
                    prompt_tokens_str=ptok_str,
                    prompt_tokens=ptok_ids,
                )
                entry.style_prompts[style_key] = variant
                return variant

            # 2. Never pitch-shift base_prompt_wav: keep 100% locked speaker vocal tract & timbre
            variant = entry.style_prompts["ke"]
            entry.style_prompts[style_key] = variant
            return variant
    @torch.inference_mode()
    def _synthesize_sentence_cached(
        self,
        sentence: str,
        entry: VoiceEntry,
        num_step: int = NUM_STEP,
        guidance_scale: float = 1.0,
        speed: float = 1.0,
        t_shift: float = 0.5,
        target_rms: float = 0.1,
        feat_scale: float = 0.1,
        max_duration: float = 100.0,
        seed: int = 666,
    ) -> torch.Tensor:
        """Synthesize a sentence using cached GPU prompt features (skips disk & pydub prompt overhead)."""
        self._ensure_cached_prompt(entry)
        text = sentence.strip()
        tokens_str = self.tts.tokenizer.texts_to_tokens([text])[0]
        if not tokens_str:
            return torch.zeros((1, int(NATIVE_SR * 0.05)))

        prompt_tokens_str = entry.prompt_tokens_str
        prompt_duration = entry.prompt_duration
        token_duration = max(prompt_duration / max(len(prompt_tokens_str) * speed, 1e-4), 0.01)
        max_tokens = max(int((25.0 - prompt_duration) / token_duration), 20)
        chunked_tokens_str = chunk_tokens_punctuation(tokens_str, max_tokens=max_tokens)
        chunked_tokens = self.tts.tokenizer.tokens_to_token_ids(chunked_tokens_str)

        tokens_batches, chunked_index = batchify_tokens(
            chunked_tokens, max_duration, prompt_duration, token_duration
        )

        chunked_features = []
        with torch.autocast(
            device_type="cuda" if DEVICE == "cuda" else "cpu", dtype=torch.float16, enabled=self.tts.use_fp16
        ):
            for batch_tokens in tokens_batches:
                fix_random_seed(seed)
                batch_prompt_tokens = entry.prompt_tokens * len(batch_tokens)
                batch_prompt_features = entry.prompt_features.repeat(len(batch_tokens), 1, 1)
                batch_prompt_features_lens = torch.full(
                    (len(batch_tokens),), entry.prompt_features.size(1), device=self.tts.device
                )
                pred_features, pred_features_lens, _, _ = self.tts.model.sample(
                    tokens=batch_tokens,
                    prompt_tokens=batch_prompt_tokens,
                    prompt_features=batch_prompt_features,
                    prompt_features_lens=batch_prompt_features_lens,
                    speed=speed,
                    t_shift=t_shift,
                    duration="predict",
                    num_step=num_step,
                    guidance_scale=guidance_scale,
                )
                pred_features = pred_features.permute(0, 2, 1) / feat_scale
                chunked_features.append((pred_features, pred_features_lens))

        chunked_wavs = []
        for pred_features, pred_features_lens in chunked_features:
            for i in range(pred_features.size(0)):
                wav = self.tts.vocoder.decode(
                    pred_features[i][None, :, : pred_features_lens[i]]
                ).squeeze(1)
                wav, _ = rms_norm(wav.cpu(), target_rms=target_rms)
                if entry.prompt_rms < target_rms:
                    wav = wav * entry.prompt_rms / target_rms
                chunked_wavs.append(wav)
        indexed = sorted(zip(chunked_index, chunked_wavs), key=lambda x: x[0])
        ordered_wavs = [w for _, w in indexed]
        final_wav = cross_fade_concat(ordered_wavs, fade_duration=0.08, sample_rate=NATIVE_SR)
        final_wav = trim_edge_silence_float32(final_wav, sampling_rate=NATIVE_SR, keep_silence_ms=60)
        return final_wav

    @staticmethod
    def _quality_check_enabled(quality_check: Optional[bool]) -> bool:
        if quality_check is not None:
            return bool(quality_check)
        return os.environ.get("VIZIPVOICE_QUALITY_CHECK", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def _qc_take_cer(self, seg: torch.Tensor, span_text: str) -> float:
        """CER of a synthesized span vs its (normalised) text via ASR."""
        hyps = qc_asr.transcribe([seg.squeeze(0).numpy()], NATIVE_SR)
        hyp = hyps[0] if hyps else ""
        ref_n = normalize_for_compare(
            span_text, normalizer=lambda t: normalize_vietnamese_text(t, enabled=True)
        )
        hyp_n = normalize_for_compare(
            hyp, normalizer=lambda t: normalize_vietnamese_text(t, enabled=True)
        )
        return qc_cer(ref_n, hyp_n)

    @torch.inference_mode()
    def synthesize_tensor(
        self,
        text: str,
        entry: VoiceEntry,
        speed: Optional[float] = None,
        style: Optional[str] = "auto",
        num_step: int = NUM_STEP,
        crossfade_ms: int = 75,
        silence_ms: int = 190,
        fade_in_ms: int = 20,
        fade_out_ms: int = 80,
        quality_check: Optional[bool] = None,
        segments_out: Optional[List[dict]] = None,
        qc_out: Optional[dict] = None,
    ) -> torch.Tensor:
        """Synthesize full text at 24kHz with Prosody Director & Multi-Emotion Prompt Bank.

        When ``segments_out`` is a list, one ``{start, end, text}`` dict (seconds at
        24 kHz, ``end`` = end of speech before the trailing pause) is appended per
        merged span. When ``quality_check`` is on (or env VIZIPVOICE_QUALITY_CHECK),
        each span is re-transcribed by ASR and regenerated with seeds 667+ while
        CER > VIZIPVOICE_QC_MAX_CER, up to VIZIPVOICE_QC_MAX_RETRIES extra takes;
        per-request stats accumulate into ``qc_out``.
        """
        base_speed = float(speed) if speed is not None else entry.recommended_speed
        clauses = parse_expressive_script(text, default_style=style)
        if not clauses:
            raise ValueError("input produced no pronounceable clauses")

        crossfade_samples = int(NATIVE_SR * max(crossfade_ms, 0) / 1000)
        fade_in_samples = int(NATIVE_SR * max(fade_in_ms, 0) / 1000)
        fade_out_samples = int(NATIVE_SR * max(fade_out_ms, 0) / 1000)

        top_style = normalize_style_name(style)
        top_spec = STYLE_SPECS.get(top_style, STYLE_SPECS["ke"]) if top_style != "auto" else STYLE_SPECS["ke"]
        call_speed = round(float(min(max(base_speed * top_spec.speed_mult, 0.55), 1.85)), 4)
        call_cfg = 1.0
        call_step = int(num_step)

        # Merge consecutive clauses into coherent multi-sentence spans (up to 60 words)
        # unless separated by an explicit dramatic pause (> 360ms) or paragraph break
        spans: List[Tuple[str, int]] = []
        for clause in clauses:
            if (
                spans
                and spans[-1][1] <= 360
                and (_count_words(spans[-1][0]) + _count_words(clause.text) <= 60)
            ):
                prev_txt, _ = spans[-1]
                spans[-1] = (f"{prev_txt} {clause.text}", clause.pause_after_ms)
            else:
                spans.append((clause.text, clause.pause_after_ms))

        qc_enabled = self._quality_check_enabled(quality_check)
        qc_active = False
        qc_max_cer = float(os.environ.get("VIZIPVOICE_QC_MAX_CER", "0.15"))
        qc_max_retries = int(os.environ.get("VIZIPVOICE_QC_MAX_RETRIES", "2"))
        if qc_enabled:
            qc_active = qc_asr.is_available()
            if not qc_active:
                log.warning(
                    "quality_check requested but ASR is unavailable; skipping QC gate"
                )

        combined: Optional[torch.Tensor] = None
        with self._gpu_lock:
            self._ensure_cached_prompt(entry)
            fix_random_seed(666)
            for idx, (span_text, span_pause_ms) in enumerate(spans):
                seg = self._synthesize_sentence_cached(
                    sentence=span_text,
                    entry=entry,
                    num_step=call_step,
                    guidance_scale=call_cfg,
                    speed=call_speed,
                )
                if qc_active:
                    best_cer = self._qc_take_cer(seg, span_text)
                    tries = 0
                    next_seed = 667
                    while best_cer > qc_max_cer and tries < qc_max_retries:
                        cand = self._synthesize_sentence_cached(
                            sentence=span_text,
                            entry=entry,
                            num_step=call_step,
                            guidance_scale=call_cfg,
                            speed=call_speed,
                            seed=next_seed,
                        )
                        next_seed += 1
                        tries += 1
                        cand_cer = self._qc_take_cer(cand, span_text)
                        if cand_cer < best_cer:
                            seg, best_cer = cand, cand_cer
                    if qc_out is not None:
                        qc_out["ran"] = True
                        qc_out["max_cer"] = max(
                            float(qc_out.get("max_cer", 0.0)), round(best_cer, 4)
                        )
                        qc_out["retries"] = int(qc_out.get("retries", 0)) + tries
                speech_samples = seg.shape[-1]
                if idx < len(spans) - 1:
                    post_ms = span_pause_ms if span_pause_ms > 0 else silence_ms
                    post_sil_samples = int(NATIVE_SR * max(post_ms, 0) / 1000)
                    if post_sil_samples > 0:
                        sil = torch.zeros(seg.shape[0], post_sil_samples, dtype=seg.dtype)
                        seg = torch.cat([seg, sil], dim=1)
                if combined is None:
                    seg_start = 0
                    combined = seg
                else:
                    fade_len = min(crossfade_samples, combined.shape[1], seg.shape[1])
                    seg_start = combined.shape[1] - fade_len
                    combined = append_with_crossfade(combined, seg, crossfade_samples)
                if segments_out is not None:
                    segments_out.append(
                        {
                            "start": round(seg_start / NATIVE_SR, 3),
                            "end": round((seg_start + speech_samples) / NATIVE_SR, 3),
                            "text": span_text,
                        }
                    )
        if combined is None:
            return torch.zeros((1, int(NATIVE_SR * 0.1)))
        combined = apply_fade(combined, fade_in_samples=fade_in_samples, fade_out_samples=fade_out_samples)
        return combined.cpu()

    def acquire(self) -> None:
        with self._counter_lock:
            if self.waiting >= self.max_streams * 16:
                raise HTTPException(429, "server busy: queue full", headers={"Retry-After": "2"})
            self.waiting += 1
        ok = self._gate.acquire(timeout=self.queue_timeout)
        with self._counter_lock:
            self.waiting -= 1
            if not ok:
                raise HTTPException(429, "server busy: timed out waiting for GPU slot", headers={"Retry-After": "2"})
            self.active += 1

    def release(self) -> None:
        with self._counter_lock:
            self.active -= 1
        self._gate.release()


engine: Optional[ViZipVoiceEngine] = None
app = FastAPI(title="ViZipVoice OpenAI-Compatible Speech API", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    global engine
    engine = ViZipVoiceEngine()
    get_jobs()


def _token_ok(header: str) -> bool:
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return hmac.compare_digest(token.encode("utf-8"), API_KEY.encode("utf-8"))


def _check_auth(request: Request) -> None:
    if not API_KEY:
        return
    if not _token_ok(request.headers.get("authorization", "")):
        raise HTTPException(401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})


_OPEN_EXACT = {("GET", "/health"), ("HEAD", "/health"), ("GET", "/"), ("HEAD", "/")}
_OPEN_PREFIX = ("/static/",)


def is_open_route(method: str, path: str) -> bool:
    """Routes reachable without a Bearer token: /health and the static UI shell (/ and /static/*)."""
    if (method.upper(), path) in _OPEN_EXACT:
        return True
    return method.upper() in ("GET", "HEAD") and path.startswith(_OPEN_PREFIX)


class BearerAuthMiddleware:
    """Pure ASGI gate (safe for streaming/SSE): every route needs the key when one is configured."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not API_KEY or is_open_route(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if _token_ok(headers.get("authorization", "")):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": "invalid or missing bearer token"}).encode("utf-8")
        await send({
            "type": "http.response.start", "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer")],
        })
        await send({"type": "http.response.body", "body": body})


app.add_middleware(BearerAuthMiddleware)


class _McpGateway:
    """ASGI endpoint for /mcp. The Streamable HTTP session manager is created per app startup
    (its run() may only be entered once per instance) and mounted here."""

    def __init__(self):
        self.handler = None
        self._cm = None

    async def start(self) -> None:
        import mcp_server
        from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
        from mcp.server.transport_security import TransportSecuritySettings

        mcp_server.configure_http_mode(f"http://127.0.0.1:{os.environ.get('PORT', '8001')}", API_KEY)
        mcp_server.mcp.streamable_http_app(
            streamable_http_path="/mcp",
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        manager = mcp_server.mcp.session_manager
        self._cm = manager.run()
        await self._cm.__aenter__()
        self.handler = StreamableHTTPASGIApp(manager)

    async def stop(self) -> None:
        self.handler = None
        if self._cm is not None:
            cm, self._cm = self._cm, None
            await cm.__aexit__(None, None, None)

    async def __call__(self, scope, receive, send):
        if self.handler is None:
            resp = JSONResponse({"detail": "MCP chưa sẵn sàng"}, status_code=503)
            await resp(scope, receive, send)
            return
        await self.handler(scope, receive, send)


mcp_gateway = _McpGateway()
app.router.routes.append(Route("/mcp", endpoint=mcp_gateway, methods=["GET", "POST", "DELETE"]))


@app.on_event("startup")
async def _mcp_startup() -> None:
    await mcp_gateway.start()


@app.on_event("shutdown")
async def _mcp_shutdown() -> None:
    await mcp_gateway.stop()


@app.get("/health")
def health():
    if engine is None:
        return JSONResponse({"status": "loading"}, status_code=503)
    return {
        "status": "ok",
        "backend": f"vizipvoice-{DEVICE}-{'fp16' if USE_FP16 else 'fp32'}",
        "max_streams": engine.max_streams,
        "active": engine.active,
        "waiting": engine.waiting,
        "sample_rate": DEFAULT_OUTPUT_SR,
        "voices": len(engine.voices),
        "chunker": CHUNKER_MODE,
    }


@app.get("/v1/models")
def list_models(request: Request):
    _check_auth(request)
    return {
        "object": "list",
        "data": [
            {"id": MODEL_ID, "object": "model", "owned_by": "contextboxai"},
        ],
    }


@app.get("/v1/voices")
def list_voices(
    request: Request,
    gender: str = "",
    region: str = "",
    kind: str = "",
    q: str = "",
):
    _check_auth(request)
    assert engine is not None
    data = engine.list_voices()
    data = voice_meta.filter_voices(
        data,
        gender=gender or None,
        region=region or None,
        kind=kind or None,
        q=q or None,
    )
    return {"object": "list", "data": data}


@app.post("/v1/voices", status_code=201)
def create_voice(
    request: Request,
    file: UploadFile = File(..., description="Reference clip (wav/mp3/flac, 3-10s)"),
    name: str = Form(..., min_length=1, max_length=80),
    denoise: bool = Form(True),
    description: str = Form(""),
    ref_text: str = Form(""),
    prompt_text: str = Form(""),
    gender: str = Form(""),
    region: str = Form(""),
    auto_transcribe: bool = Form(True),
    force: bool = Form(False),
    consent: bool = Form(False),
):
    _check_auth(request)
    assert engine is not None
    if not consent:
        raise HTTPException(
            400,
            "Cần consent=true: chỉ clone giọng khi người đó đồng ý và không dùng để mạo danh.",
        )
    name_clean = nfc(name)
    if name_clean in engine.voices and not force:
        raise HTTPException(409, f"voice {name_clean!r} already exists")

    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "empty audio file")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(413, "reference audio exceeds 25 MB limit")

    resolved_prompt = (ref_text or prompt_text).strip()
    engine.acquire()
    try:
        entry = engine.enroll_voice(
            name=name_clean,
            wav_bytes=raw,
            prompt_text=resolved_prompt,
            description=description,
            force=force,
            gender=gender,
            region=region,
            auto_transcribe=auto_transcribe,
        )
    except Exception as exc:
        raise HTTPException(400, f" could not process reference audio: {exc}") from exc
    finally:
        engine.release()

    duration_s = 0.0
    try:
        info = sf.info(str(entry.wav_path))
        duration_s = round(float(info.frames) / float(info.samplerate), 3)
    except Exception:
        pass
    return {
        "id": entry.name,
        "name": entry.name,
        "description": entry.description,
        "status": "created",
        "prompt_text": entry.prompt_text,
        "transcript_source": entry.transcript_source,
        "gender": entry.gender,
        "region": entry.region,
        "duration_s": duration_s,
    }


def _tensor_to_int16_pcm(wav_1d: torch.Tensor, orig_sr: int, target_sr: int) -> np.ndarray:
    _, pcm_i16, _ = finalize_waveform_for_export(wav_1d, orig_sr=orig_sr, target_sr=target_sr)
    return pcm_i16


MULTI_VOICE_JOIN_MS = 250


def _qc_headers(qc: dict) -> Dict[str, str]:
    if not qc.get("ran"):
        return {}
    return {
        "X-QC-Max-CER": f"{float(qc.get('max_cer', 0.0)):.4f}",
        "X-QC-Retries": str(int(qc.get("retries", 0))),
    }


# ---------------------------------------------------------------------------
# Studio state (history, jobs, music) — lazily bound to the current TQD home
# ---------------------------------------------------------------------------
_STORE: Optional[tqd_store.Store] = None
_JOBS: Optional[tqd_jobs.JobManager] = None
_STATE_LOCK = threading.Lock()


def get_store() -> tqd_store.Store:
    global _STORE
    with _STATE_LOCK:
        if _STORE is None or _STORE.db_path != paths.db_path():
            _STORE = tqd_store.Store(paths.db_path())
        return _STORE


def get_jobs() -> tqd_jobs.JobManager:
    global _JOBS
    store = get_store()
    with _STATE_LOCK:
        if _JOBS is None or _JOBS.store is not store:
            if _JOBS is not None:
                _JOBS.stop()
            _JOBS = tqd_jobs.JobManager(
                store,
                paths.home() / "jobs",
                render=_job_render,
                assemble=_job_assemble,
                webhook_secret=tqd_config.ensure_webhook_secret,
            )
        return _JOBS


def _history_dir() -> Path:
    d = paths.home() / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _music_dir() -> Path:
    d = paths.home() / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lexicon_hash() -> str:
    entries = lexicon.get()
    blob = json.dumps(entries, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_key(req: SpeechRequest, kind: str) -> str:
    payload = {
        "kind": kind,
        "input": req.input,
        "voice": req.voice,
        "speed": req.speed,
        "style": req.style,
        "model": req.model,
        "response_format": req.response_format,
        "sample_rate": req.sample_rate,
        "normalize": req.normalize,
        "trim_silence": req.trim_silence,
        "music": req.music.model_dump() if req.music else None,
        "cast": dict(sorted(req.cast.items())) if req.cast else None,
        "lexicon": _lexicon_hash(),
        "quality_check": req.quality_check,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _prepare_text(text: str, cast: Optional[Dict[str, str]]) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(400, "input must not be empty")
    if cast:
        try:
            return script_roles.apply_cast(text, cast)
        except KeyError as exc:
            missing = exc.args[0]
            raise HTTPException(
                400,
                f"vai chưa gán giọng: {', '.join(missing)}. "
                f"Các vai trong văn bản: {', '.join(script_roles.parse_roles(text))}",
            ) from exc
    return text


def _synthesize_runs(
    req: SpeechRequest,
    text: Optional[str] = None,
) -> Tuple[torch.Tensor, List[dict], dict, List[Tuple[int, int]]]:
    """Split the input on [voice=...] tags and [pause:N] markers, resolve all voices up front,
    synthesize each run with its own voice and join runs with 250 ms of silence (explicit
    pauses are exact silences).

    Returns (wav_24k tensor, timed segments, qc stats dict, explicit pause ranges in samples).
    """
    text = text if text is not None else _prepare_text(req.input, req.cast)

    runs = script_voices.split_voice_runs(text, req.voice)
    if not runs:
        raise HTTPException(400, "input produced no speakable text")

    run_entries: List[VoiceEntry] = []
    run_parts = []
    for run_voice, run_text in runs:
        try:
            run_entries.append(engine.resolve_voice(run_voice))
        except KeyError:
            raise HTTPException(
                400,
                f"unknown voice {run_voice!r}; see GET /v1/voices",
            )
        try:
            run_parts.append(pauses.split_pauses(run_text))
        except pauses.PauseError as exc:
            raise HTTPException(400, str(exc)) from exc

    join_samples = int(NATIVE_SR * MULTI_VOICE_JOIN_MS / 1000)
    segments: List[dict] = []
    pause_ranges: List[Tuple[int, int]] = []
    qc: dict = {"ran": False, "max_cer": 0.0, "retries": 0}
    pieces: List[torch.Tensor] = []
    offset_samples = 0
    prev_speech = False
    for run_entry, parts in zip(run_entries, run_parts):
        for kind, value in parts:
            if kind == "pause":
                n = int(round(float(value) * NATIVE_SR))
                pieces.append(torch.zeros(1, n))
                pause_ranges.append((offset_samples, offset_samples + n))
                offset_samples += n
                prev_speech = False
                continue
            if prev_speech:
                pieces.append(torch.zeros(1, join_samples))
                offset_samples += join_samples
            part_segments: List[dict] = []
            wav = engine.synthesize_tensor(
                text=value,
                entry=run_entry,
                speed=req.speed,
                style=req.style,
                quality_check=req.quality_check,
                segments_out=part_segments,
                qc_out=qc,
            )
            for s in part_segments:
                segments.append(
                    {
                        "start": round(s["start"] + offset_samples / NATIVE_SR, 3),
                        "end": round(s["end"] + offset_samples / NATIVE_SR, 3),
                        "text": s["text"],
                        "voice": run_entry.name,
                    }
                )
            pieces.append(wav)
            offset_samples += wav.shape[-1]
            prev_speech = True

    if not pieces:
        raise HTTPException(400, "input produced no speakable text")
    combined = pieces[0] if len(pieces) == 1 else torch.cat(pieces, dim=1)
    return combined, segments, qc, pause_ranges


def _load_music(music_id: str) -> np.ndarray:
    row = get_store().get_music(music_id)
    if not row or not Path(row["path"]).is_file():
        raise HTTPException(400, f"unknown music id {music_id!r}; see GET /v1/music")
    wav, sr = sf.read(row["path"], dtype="float32", always_2d=True)
    mono = torch.from_numpy(wav.mean(axis=1)).unsqueeze(0)
    if sr != NATIVE_SR:
        mono = torchaudio.functional.resample(mono, sr, NATIVE_SR)
    return mono.squeeze(0).numpy()


def _finalize_audio(
    req: SpeechRequest,
    wav: np.ndarray,
    segments: List[dict],
    pause_ranges: List[Tuple[int, int]],
) -> Tuple[np.ndarray, List[dict]]:
    """Post-processing at 24 kHz: trim_silence -> music -> normalize (segments are remapped)."""
    x = np.asarray(wav, dtype=np.float32).reshape(-1)
    if req.trim_silence:
        x, cuts = mixing.trim_silences(x, NATIVE_SR, protected=pause_ranges)
        if cuts:
            segments = [
                {**s, "start": mixing.remap_time(s["start"], cuts, NATIVE_SR),
                 "end": mixing.remap_time(s["end"], cuts, NATIVE_SR)}
                for s in segments
            ]
    if req.music:
        music = _load_music(req.music.id)
        x = mixing.mix_music(
            x, music, NATIVE_SR,
            volume_db=req.music.volume_db, duck_db=req.music.duck_db, fade_s=req.music.fade_s,
        )
    if req.normalize:
        x = mixing.normalize_loudness(x, NATIVE_SR)
    return x, segments


def _store_output(
    req: SpeechRequest,
    kind: str,
    wav24: np.ndarray,
    segments: List[dict],
    cache_key: str,
) -> dict:
    pcm_i16 = _tensor_to_int16_pcm(torch.from_numpy(np.asarray(wav24, dtype=np.float32)), NATIVE_SR, req.sample_rate)
    raw = pcm_i16.tobytes()
    if req.response_format == "pcm":
        data, media_type = raw, f"audio/pcm;rate={req.sample_rate};encoding=signed-int;bits=16"
    else:
        try:
            data, media_type = audio_formats.encode_pcm16(raw, req.sample_rate, req.response_format)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
    duration = len(pcm_i16) / float(req.sample_rate) if len(pcm_i16) else 0.0
    srt = subtitles.segments_to_srt(segments)
    vtt = subtitles.segments_to_vtt(segments)
    store = get_store()
    hid = tqd_store.new_id()
    path = _history_dir() / f"{hid}.{req.response_format}"
    path.write_bytes(data)
    store.add_history(
        id=hid, kind=kind, cache_key=cache_key, text=req.input, voice=req.voice, model=req.model,
        response_format=req.response_format, sample_rate=req.sample_rate, duration_s=round(duration, 3),
        audio_path=str(path), media_type=media_type, segments_json=json.dumps(segments, ensure_ascii=False),
        srt=srt, vtt=vtt, params_json=json.dumps({"speed": req.speed, "style": req.style}, ensure_ascii=False),
    )
    store.prune_history(tqd_config.load().history_max)
    return {"history_id": hid, "data": data, "media_type": media_type, "duration_s": round(duration, 3),
            "segments": segments, "srt": srt, "vtt": vtt}


def _row_to_output(row: dict) -> dict:
    return {
        "history_id": row["id"],
        "data": Path(row["audio_path"]).read_bytes(),
        "media_type": row["media_type"],
        "duration_s": row["duration_s"],
        "segments": json.loads(row["segments_json"] or "[]"),
        "srt": row["srt"],
        "vtt": row["vtt"],
    }


def _audio_response(out: dict, req: SpeechRequest, headers: Dict[str, str]):
    if req.response_format != "pcm":
        return Response(content=out["data"], media_type=out["media_type"], headers=headers)
    raw = out["data"]
    chunk_bytes = PCM_CHUNK_SAMPLES * 2

    def _pcm_iter() -> Iterator[bytes]:
        for offset in range(0, len(raw), chunk_bytes):
            yield raw[offset : offset + chunk_bytes]

    return StreamingResponse(_pcm_iter(), media_type=out["media_type"], headers=headers)


def _produce(req: SpeechRequest, kind: str) -> Tuple[dict, Dict[str, str]]:
    """Cache lookup -> synth -> post-process -> encode -> history. Returns (output, headers)."""
    if req.sample_rate not in SUPPORTED_RATES:
        raise HTTPException(400, f"sample_rate must be one of {SUPPORTED_RATES}")
    store = get_store()
    key = _cache_key(req, kind)
    headers = {"X-Sample-Rate": str(req.sample_rate)}
    if req.cache:
        row = store.find_cached(key)
        if row:
            headers.update({"X-TQD-Cache": "hit", "X-TQD-History-Id": row["id"]})
            return _row_to_output(row), headers

    assert engine is not None
    engine.acquire()
    t0 = time.perf_counter()
    try:
        wav_24k, segments, qc, pause_ranges = _synthesize_runs(req)
    finally:
        engine.release()
    x, segments = _finalize_audio(req, wav_24k.squeeze(0).numpy(), segments, pause_ranges)
    out = _store_output(req, kind, x, segments, key)
    log.info(
        "speech done: voice=%s fmt=%s sr=%d synth=%.2fs audio=%.2fs",
        req.voice, req.response_format, req.sample_rate, time.perf_counter() - t0, out["duration_s"],
    )
    headers.update(_qc_headers(qc))
    headers.update({"X-TQD-Cache": "miss", "X-TQD-History-Id": out["history_id"]})
    return out, headers


# ---- job seams -------------------------------------------------------------
def _job_render(request: dict, seg: dict):
    req = SpeechRequest(
        **{**request, "input": seg["text"], "voice": seg["voice"], "cast": None,
           "normalize": False, "trim_silence": False, "music": None}
    )
    assert engine is not None
    engine.acquire()
    try:
        wav, segments, _qc, pause_ranges = _synthesize_runs(req, text=seg["text"])
    finally:
        engine.release()
    return wav.squeeze(0).numpy(), segments, pause_ranges


def _job_assemble(request: dict, wav: np.ndarray, segments: List[dict], pauses_: List[Tuple[int, int]], subs: bool) -> str:
    req = SpeechRequest(**request)
    x, segments = _finalize_audio(req, wav, segments, pauses_)
    return _store_output(req, "job", x, segments, _cache_key(req, "job"))["history_id"]


@app.post("/v1/audio/speech")
def create_speech(req: SpeechRequest, request: Request):
    _check_auth(request)
    out, headers = _produce(req, "speech")
    return _audio_response(out, req, headers)


@app.post("/v1/audio/speech/timed")
def create_speech_timed(req: TimedSpeechRequest, request: Request):
    _check_auth(request)
    out, headers = _produce(req, "timed")
    body = {
        "audio_base64": base64.b64encode(out["data"]).decode("ascii"),
        "response_format": req.response_format,
        "media_type": out["media_type"],
        "sample_rate": req.sample_rate,
        "duration_s": out["duration_s"],
        "segments": out["segments"],
    }
    sub_fmt = (req.subtitle_format or "both").strip().lower()
    if sub_fmt in ("srt", "both"):
        body["srt"] = out["srt"]
    if sub_fmt in ("vtt", "both"):
        body["vtt"] = out["vtt"]
    return JSONResponse(body, headers=headers)


@app.post("/v1/script/roles")
def script_roles_endpoint(body: RolesBody, request: Request):
    _check_auth(request)
    return {"roles": script_roles.parse_roles(body.input)}


# ---- jobs ------------------------------------------------------------------
@app.post("/v1/jobs", status_code=202)
def create_job(req: JobRequest, request: Request):
    _check_auth(request)
    assert engine is not None
    if req.sample_rate not in SUPPORTED_RATES:
        raise HTTPException(400, f"sample_rate must be one of {SUPPORTED_RATES}")
    if req.webhook_url and not re.match(r"^https?://", req.webhook_url):
        raise HTTPException(400, "webhook_url phải bắt đầu bằng http:// hoặc https://")
    text = _prepare_text(req.input, req.cast)
    runs = script_voices.split_voice_runs(text, req.voice)
    if not runs:
        raise HTTPException(400, "input produced no speakable text")
    for run_voice, run_text in runs:
        try:
            engine.resolve_voice(run_voice)
        except KeyError:
            raise HTTPException(400, f"unknown voice {run_voice!r}; see GET /v1/voices")
        try:
            pauses.split_pauses(run_text)
        except pauses.PauseError as exc:
            raise HTTPException(400, str(exc)) from exc
    if req.music and not get_store().get_music(req.music.id):
        raise HTTPException(400, f"unknown music id {req.music.id!r}; see GET /v1/music")
    plan = tqd_jobs.plan_segments(text, req.voice, script_voices.split_voice_runs)
    request_dict = req.model_dump(exclude={"webhook_url", "subtitles"})
    request_dict.update({"input": text, "cast": None})
    jobs = get_jobs()
    jid = jobs.submit(request_dict, plan, req.webhook_url, req.subtitles)
    return {"id": jid, "status": "queued"}


def _job_or_404(jid: str) -> dict:
    row = get_store().get_job(jid)
    if not row:
        raise HTTPException(404, "job not found")
    return row


def _job_history(jid: str) -> dict:
    row = _job_or_404(jid)
    if row["status"] != "done" or not row["history_id"]:
        raise HTTPException(409, f"job chưa xong (trạng thái: {row['status']})")
    hist = get_store().get_history(row["history_id"])
    if not hist:
        raise HTTPException(404, "kết quả của job đã bị xóa khỏi lịch sử")
    return hist


@app.get("/v1/jobs")
def list_jobs(request: Request):
    _check_auth(request)
    jobs = get_jobs()
    return {"object": "list", "data": [jobs.view(r) for r in get_store().list_jobs()]}


@app.get("/v1/jobs/{jid}")
def get_job(jid: str, request: Request):
    _check_auth(request)
    return get_jobs().view(_job_or_404(jid))


@app.get("/v1/jobs/{jid}/audio")
def job_audio(jid: str, request: Request):
    _check_auth(request)
    hist = _job_history(jid)
    return Response(content=Path(hist["audio_path"]).read_bytes(), media_type=hist["media_type"])


@app.get("/v1/jobs/{jid}/subtitles.srt")
def job_srt(jid: str, request: Request):
    _check_auth(request)
    hist = _job_history(jid)
    return Response(content=hist["srt"] or "", media_type="application/x-subrip; charset=utf-8")


@app.get("/v1/jobs/{jid}/subtitles.vtt")
def job_vtt(jid: str, request: Request):
    _check_auth(request)
    hist = _job_history(jid)
    return Response(content=hist["vtt"] or "", media_type="text/vtt; charset=utf-8")


@app.post("/v1/jobs/{jid}/cancel")
def cancel_job(jid: str, request: Request):
    _check_auth(request)
    _job_or_404(jid)
    status = get_jobs().cancel(jid)
    return {"id": jid, "status": status}


# ---- history + ratings -----------------------------------------------------
@app.get("/v1/history")
def list_history(request: Request, limit: int = 50, offset: int = 0):
    _check_auth(request)
    limit = max(1, min(limit, 500))
    return {"object": "list", "data": get_store().list_history(limit, max(offset, 0))}


@app.get("/v1/history/{hid}/audio")
def history_audio(hid: str, request: Request):
    _check_auth(request)
    row = get_store().get_history(hid)
    if not row or not Path(row["audio_path"]).is_file():
        raise HTTPException(404, "history item not found")
    return Response(content=Path(row["audio_path"]).read_bytes(), media_type=row["media_type"])


@app.delete("/v1/history/{hid}")
def delete_history(hid: str, request: Request):
    _check_auth(request)
    if not get_store().delete_history(hid):
        raise HTTPException(404, "history item not found")
    return {"id": hid, "deleted": True}


@app.post("/v1/ratings", status_code=201)
def create_rating(body: RatingBody, request: Request):
    _check_auth(request)
    store = get_store()
    if not body.history_id and not body.job_id:
        raise HTTPException(400, "cần history_id hoặc job_id")
    text, voice, model, history_id, job_id = "", "", "", body.history_id, body.job_id
    if body.job_id:
        job = _job_or_404(body.job_id)
        req = json.loads(job["request_json"])
        plan = json.loads(job["plan_json"])
        history_id = job["history_id"]
        text, voice, model = req.get("input", ""), req.get("voice", ""), req.get("model", "")
        if body.segment_index is not None:
            if not 0 <= body.segment_index < len(plan):
                raise HTTPException(400, "segment_index ngoài phạm vi")
            text, voice = plan[body.segment_index]["text"], plan[body.segment_index]["voice"]
    else:
        hist = store.get_history(body.history_id)
        if not hist:
            raise HTTPException(404, "history item not found")
        text, voice, model = hist["text"], hist["voice"], hist["model"]
        if body.segment_index is not None:
            segs = json.loads(hist["segments_json"] or "[]")
            if not 0 <= body.segment_index < len(segs):
                raise HTTPException(400, "segment_index ngoài phạm vi")
            text, voice = segs[body.segment_index]["text"], segs[body.segment_index].get("voice", voice)
    rid = store.add_rating(
        history_id=history_id, job_id=job_id, segment_index=body.segment_index,
        rating=body.rating, note=body.note, text=text, voice=voice, model=model,
    )
    return {"id": rid}


@app.get("/v1/ratings")
def list_ratings(request: Request, format: str = "json"):
    _check_auth(request)
    rows = get_store().list_ratings()
    if format == "jsonl":
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        return Response(content=body, media_type="application/x-ndjson; charset=utf-8")
    if format != "json":
        raise HTTPException(400, "format phải là json hoặc jsonl")
    return {"object": "list", "data": rows}


# ---- music -----------------------------------------------------------------
@app.post("/v1/music", status_code=201)
def upload_music(request: Request, file: UploadFile = File(...), name: str = Form("")):
    _check_auth(request)
    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "empty audio file")
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(413, "nhạc nền vượt quá giới hạn 50 MB")
    try:
        wav, sr = audio_formats.decode_audio(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    mid = tqd_store.new_id()
    path = _music_dir() / f"{mid}.wav"
    sf.write(str(path), wav, sr, subtype="PCM_16")
    duration = round(len(wav) / float(sr), 3)
    label = name.strip() or (file.filename or mid)
    get_store()._exec(
        "INSERT INTO music (id,name,duration,path,created_at) VALUES (?,?,?,?,?)",
        (mid, label, duration, str(path), time.time()),
    )
    return {"id": mid, "name": label, "duration": duration}


@app.get("/v1/music")
def list_music(request: Request):
    _check_auth(request)
    return {"object": "list", "data": get_store().list_music()}


@app.delete("/v1/music/{mid}")
def delete_music(mid: str, request: Request):
    _check_auth(request)
    if not get_store().delete_music(mid):
        raise HTTPException(404, "music not found")
    return {"id": mid, "deleted": True}


# ---- import ----------------------------------------------------------------
@app.post("/v1/import")
async def import_text(request: Request):
    _check_auth(request)
    ctype = request.headers.get("content-type", "")
    try:
        if ctype.startswith("application/json"):
            body = await request.json()
            url = (body or {}).get("url", "")
            if not url:
                raise HTTPException(400, "cần trường url")
            return await run_in_threadpool(importers.import_url, url)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(400, "cần file (.txt .md .docx .pdf) hoặc JSON {url}")
        data = await upload.read()
        return await run_in_threadpool(importers.import_file, upload.filename or "", data)
    except importers.MissingDependency as exc:
        raise HTTPException(501, str(exc)) from exc
    except importers.ImportFailure as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/v1/voices/{name}")
def delete_voice(name: str, request: Request):
    _check_auth(request)
    assert engine is not None
    try:
        entry = engine.delete_voice(nfc(name))
    except KeyError:
        raise HTTPException(404, f"voice {name!r} not found")
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"id": entry.name, "deleted": True}


WEB_DIR = Path(tqd_voicestudio.__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
def web_index():
    index = WEB_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(404, "web UI not installed")
    return Response(content=index.read_bytes(), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@app.get("/static/{path:path}", include_in_schema=False)
def web_static(path: str):
    root = WEB_DIR.resolve()
    target = (WEB_DIR / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    ctype = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
             ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8"}.get(target.suffix.lower(), "application/octet-stream")
    return Response(content=target.read_bytes(), media_type=ctype, headers={"Cache-Control": "no-cache"})


@app.get("/v1/lexicon")
def get_lexicon(request: Request):
    _check_auth(request)
    return {"entries": lexicon.get()}


@app.put("/v1/lexicon")
def put_lexicon(body: LexiconBody, request: Request):
    _check_auth(request)
    if not isinstance(body.entries, dict):
        raise HTTPException(400, "entries must be an object of string -> string")
    lexicon.replace(body.entries)
    return {"entries": lexicon.get(), "updated": len(body.entries)}


def main() -> None:
    host = os.environ.get("PORT_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8001"))
    if netsec.requires_key(host, API_KEY):
        print(netsec.REFUSE_MESSAGE.format(host=host), file=sys.stderr)
        raise SystemExit(2)
    uvicorn.run(app, host=host, port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
