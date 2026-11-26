"""Length-aware text chunker: groups sentences into 13-25 word chunks."""

from __future__ import annotations

import re
from typing import List

WORD_RE = re.compile(r"\w+", re.UNICODE)
_SPLIT_POINT_RE = re.compile(r"[;:,]\s+")


def weight(text: str) -> int:
    total = 0
    for token in WORD_RE.findall(text):
        digits = sum(ch.isdigit() for ch in token)
        total += min(6, max(1, digits)) if digits else 1
    return total


def split_long(sentence: str, max_words: int = 25) -> List[str]:
    sentence = sentence.strip()
    if weight(sentence) <= max_words:
        return [sentence] if sentence else []
    total = weight(sentence)
    half = total / 2
    best = None
    best_gap = None
    for m in _SPLIT_POINT_RE.finditer(sentence):
        left, right = sentence[: m.end()], sentence[m.end():]
        wl, wr = weight(left), weight(right)
        if wl < 6 or wr < 6:
            continue
        gap = abs(wl - half)
        if best_gap is None or gap < best_gap:
            best, best_gap = m.end(), gap
    if best is None:
        for m in re.finditer(r"\s+", sentence):
            left, right = sentence[: m.start()], sentence[m.end():]
            if not left.strip() or not right.strip():
                continue
            gap = abs(weight(left) - half)
            if best_gap is None or gap < best_gap:
                best, best_gap = m.start(), gap
        if best is None:
            return [sentence]
    parts = [sentence[:best].strip(), sentence[best:].strip()]
    out: List[str] = []
    for part in parts:
        if part:
            out.extend(split_long(part, max_words))
    return out


def chunk_sentences(
    sentences: List[str],
    min_words: int = 13,
    max_words: int = 25,
    tail_slack: int = 5,
) -> List[str]:
    pieces = [p for s in sentences for p in split_long(s.strip(), max_words) if p.strip()]
    out: List[str] = []
    cur = ""
    for p in pieces:
        if not cur:
            cur = p
        elif weight(cur) < min_words and weight(cur) + weight(p) <= max_words:
            cur = cur + " " + p
        else:
            out.append(cur)
            cur = p
    if out and cur and weight(cur) < min_words and weight(out[-1]) + weight(cur) <= max_words + tail_slack:
        out[-1] += " " + cur
    elif cur:
        out.append(cur)
    return out
