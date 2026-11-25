"""Lazy singleton Vietnamese ASR (PhoWhisper) plus text-compare helpers.

Used by the API server for enrollment auto-transcription and for the
post-synthesis CER quality gate. The model is only loaded on first use so
unit tests and normal synthesis never pay the startup cost.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Callable, List, Optional

import numpy as np

ASR_TARGET_SR = 16000
DEFAULT_ASR_MODEL = "vinai/PhoWhisper-small"

_pipeline = None
_pipeline_tried = False
_ct2 = None
_ct2_tried = False


def _asr_backend() -> str:
    return os.environ.get("VIZIPVOICE_ASR_BACKEND", "transformers").lower()


def _get_ct2():
    """Lazy ctranslate2 Whisper backend (batched generate; ~2x pipeline speed).

    Model dir from ``VIZIPVOICE_CT2_MODEL`` (default
    ``D:/TTS/models/phowhisper-small-ct2``); feature extractor and tokenizer
    still come from ``VIZIPVOICE_ASR_MODEL``."""
    global _ct2, _ct2_tried
    if _ct2_tried:
        return _ct2
    _ct2_tried = True
    try:
        import torch
        from pathlib import Path

        torch_lib = Path(torch.__file__).resolve().parent / "lib"
        if torch_lib.is_dir() and hasattr(os, "add_dll_directory"):
            # ctranslate2 resolves cuDNN/cuBLAS at extension load time.
            os.add_dll_directory(str(torch_lib))
        import ctranslate2
        from faster_whisper.tokenizer import Tokenizer as FWTokenizer
        from transformers import WhisperFeatureExtractor, WhisperTokenizer

        model_dir = os.environ.get(
            "VIZIPVOICE_CT2_MODEL", "D:/TTS/models/phowhisper-small-ct2"
        )
        model_id = os.environ.get("VIZIPVOICE_ASR_MODEL", DEFAULT_ASR_MODEL)
        lang = os.environ.get("VIZIPVOICE_ASR_LANG", "vi")
        cuda = torch.cuda.is_available()
        model = ctranslate2.models.Whisper(
            model_dir,
            device="cuda" if cuda else "cpu",
            compute_type="float16" if cuda else "int8",
        )
        fe = WhisperFeatureExtractor.from_pretrained(model_id)
        tok = WhisperTokenizer.from_pretrained(model_id)
        inner = getattr(tok, "_tokenizer", None) or getattr(tok, "tokenizer", tok)
        fw_tok = FWTokenizer(inner, multilingual=True, task="transcribe", language=lang)
        beam = int(os.environ.get("VIZIPVOICE_CT2_BEAM_SIZE", "1"))
        _ct2 = (model, fe, tok, fw_tok.sot_sequence, beam)
    except Exception as exc:  # pragma: no cover - depends on local env
        import logging

        logging.getLogger("vizipvoice.asr").warning("CT2 ASR unavailable: %s", exc)
        _ct2 = None
    return _ct2


def _transcribe_ct2(wavs: List[np.ndarray], sr: int, batch_size: int) -> List[str]:
    import ctranslate2

    model, fe, tok, sot, beam = _ct2
    inputs = [_resample_to_16k(w, sr) for w in wavs]
    feats = [
        fe(w, sampling_rate=ASR_TARGET_SR, return_tensors="np").input_features[0]
        for w in inputs
    ]
    out: List[str] = []
    for i in range(0, len(feats), max(batch_size, 1)):
        chunk = feats[i : i + batch_size]
        view = ctranslate2.StorageView.from_array(np.stack(chunk))
        results = model.generate(view, [sot] * len(chunk), beam_size=beam)
        out.extend(
            tok.decode(r.sequences_ids[0], skip_special_tokens=True).strip()
            for r in results
        )
    return out


def _get_pipeline():
    """Build the transformers ASR pipeline once, on first use."""
    global _pipeline, _pipeline_tried
    if _pipeline_tried:
        return _pipeline
    _pipeline_tried = True
    try:
        import torch
        from transformers import pipeline

        model_id = os.environ.get("VIZIPVOICE_ASR_MODEL", DEFAULT_ASR_MODEL)
        kwargs = {}
        if torch.cuda.is_available():
            kwargs["device"] = 0
            try:
                import inspect

                if "dtype" in inspect.signature(pipeline).parameters:
                    kwargs["dtype"] = torch.float16
                else:
                    kwargs["torch_dtype"] = torch.float16
            except (TypeError, ValueError):
                kwargs["torch_dtype"] = torch.float16
        _pipeline = pipeline("automatic-speech-recognition", model=model_id, **kwargs)
    except Exception as exc:  # pragma: no cover - depends on local env
        import logging

        logging.getLogger("vizipvoice.asr").warning("ASR pipeline unavailable: %s", exc)
        _pipeline = None
    return _pipeline


def is_available() -> bool:
    """True if the configured ASR backend could be (or has been) constructed."""
    if _asr_backend() == "ct2":
        return _get_ct2() is not None
    return _get_pipeline() is not None


def reset() -> None:
    """Drop the cached pipelines (mainly for tests)."""
    global _pipeline, _pipeline_tried, _ct2, _ct2_tried
    _pipeline = None
    _pipeline_tried = False
    _ct2 = None
    _ct2_tried = False


def _resample_to_16k(wav: np.ndarray, sr: int) -> np.ndarray:
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    if sr == ASR_TARGET_SR or wav.size == 0:
        return wav
    n_out = max(int(round(wav.size * ASR_TARGET_SR / float(sr))), 1)
    x_old = np.linspace(0.0, 1.0, num=wav.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, wav).astype(np.float32)


def transcribe(wavs: List[np.ndarray], sr: int, batch_size: int = 8) -> List[str]:
    """Transcribe a list of mono float waveforms; resamples to 16 kHz internally."""
    if _asr_backend() == "ct2":
        if _get_ct2() is None:
            raise RuntimeError("ASR (ct2) is not available")
        return _transcribe_ct2(wavs, sr, batch_size)
    pipe = _get_pipeline()
    if pipe is None:
        raise RuntimeError("ASR pipeline is not available")
    inputs = [
        {"raw": _resample_to_16k(w, sr), "sampling_rate": ASR_TARGET_SR} for w in wavs
    ]
    if not inputs:
        return []
    outputs = pipe(inputs, batch_size=batch_size)
    if isinstance(outputs, dict):
        outputs = [outputs]
    return [str(o.get("text", "")).strip() for o in outputs]


_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+", flags=re.UNICODE)


def normalize_for_compare(
    text: str, normalizer: Optional[Callable[[str], str]] = None
) -> str:
    """NFC + lowercase + strip punctuation + collapse spaces.

    ``normalizer`` (e.g. ``zipvoice.vizipvoice.normalize_vietnamese_text``) is
    applied first when given so both sides share the same text normalisation.
    """
    if normalizer is not None:
        text = normalizer(text)
    text = unicodedata.normalize("NFC", text).lower()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(
                min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1))
            )
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    """Character error rate: Levenshtein distance / len(ref)."""
    ref = ref or ""
    hyp = hyp or ""
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / float(len(ref))
