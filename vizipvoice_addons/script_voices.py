"""Split an input script into per-voice runs on ``[voice=Name]`` / ``[giọng=Name]`` tags."""

from __future__ import annotations

import re
from typing import List, Tuple

_VOICE_TAG_RE = re.compile(
    r"\[\s*(?:voice|giọng|giong)\s*=\s*([^\[\]]+?)\s*\]",
    flags=re.IGNORECASE | re.UNICODE,
)


def split_voice_runs(text: str, default_voice: str) -> List[Tuple[str, str]]:
    """Return ``[(voice, text), ...]``; empty runs are dropped.

    Text before the first tag belongs to ``default_voice``. Voice names may
    contain diacritics, spaces and hyphens.
    """
    runs: List[Tuple[str, str]] = []
    current = default_voice
    pos = 0
    for m in _VOICE_TAG_RE.finditer(text):
        seg = text[pos : m.start()]
        if seg.strip():
            runs.append((current, seg))
        name = m.group(1).strip()
        if name:
            current = name
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        runs.append((current, tail))
    return [(v, t) for v, t in runs if t.strip()]
