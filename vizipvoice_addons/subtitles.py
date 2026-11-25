"""Build SRT / WebVTT cues from synthesised segments.

Each segment is ``{"start": float, "end": float, "text": str, "voice": str}``
(times in seconds). A segment's text is split into cues of <= 12 words, with
cue times distributed proportionally to character count.
"""

from __future__ import annotations

import re
from typing import Dict, List

MAX_CUE_WORDS = 12
_CLAUSE_END = re.compile(r"[.!?…;:]+$")


def _split_text_to_cues(text: str, max_words: int = MAX_CUE_WORDS) -> List[str]:
    """Split text into cue strings of at most ``max_words`` words each.

    Prefers breaking after clause-ending punctuation once a cue has at least
    four words; otherwise breaks at the word limit.
    """
    words = text.split()
    if not words:
        return []
    cues: List[str] = []
    cur: List[str] = []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or (len(cur) >= 4 and _CLAUSE_END.search(w)):
            cues.append(" ".join(cur))
            cur = []
    if cur:
        cues.append(" ".join(cur))
    return cues


def segment_to_cues(segment: Dict) -> List[Dict]:
    """Expand one segment into timed cues proportional to character count."""
    start = float(segment["start"])
    end = float(segment["end"])
    text = str(segment.get("text", "")).strip()
    cue_texts = _split_text_to_cues(text)
    if not cue_texts:
        return []
    total_chars = sum(max(len(t), 1) for t in cue_texts)
    span = max(end - start, 0.0)
    cues = []
    cursor = start
    for i, ct in enumerate(cue_texts):
        if i == len(cue_texts) - 1:
            c_end = end
        else:
            c_end = cursor + span * (max(len(ct), 1) / total_chars)
        cues.append({"start": cursor, "end": c_end, "text": ct})
        cursor = c_end
    return cues


def build_cues(segments: List[Dict]) -> List[Dict]:
    cues: List[Dict] = []
    for seg in segments:
        cues.extend(segment_to_cues(seg))
    return cues


def _fmt_srt_ts(t: float) -> str:
    ms = max(int(round(t * 1000.0)), 0)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_ts(t: float) -> str:
    ms = max(int(round(t * 1000.0)), 0)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def segments_to_srt(segments: List[Dict]) -> str:
    cues = build_cues(segments)
    blocks = []
    for i, c in enumerate(cues, start=1):
        blocks.append(
            f"{i}\n{_fmt_srt_ts(c['start'])} --> {_fmt_srt_ts(c['end'])}\n{c['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def segments_to_vtt(segments: List[Dict]) -> str:
    cues = build_cues(segments)
    blocks = ["WEBVTT", ""]
    for c in cues:
        blocks.append(f"{_fmt_vtt_ts(c['start'])} --> {_fmt_vtt_ts(c['end'])}")
        blocks.append(c["text"])
        blocks.append("")
    return "\n".join(blocks)
