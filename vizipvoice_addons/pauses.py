"""[pause:N] markers: exact silences of 0 < N <= 10 seconds."""

from __future__ import annotations

import re
from typing import List, Tuple, Union

MAX_PAUSE_S = 10.0
_PAUSE_RE = re.compile(r"\[\s*pause\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:s|giây)?\s*\]", re.IGNORECASE)

Part = Tuple[str, Union[str, float]]


class PauseError(ValueError):
    pass


def split_pauses(text: str) -> List[Part]:
    """Split into ``("text", str)`` and ``("pause", seconds)`` parts, in order.

    Raises ``PauseError`` for a value outside 0 < N <= 10.
    """
    parts: List[Part] = []
    pos = 0
    for m in _PAUSE_RE.finditer(text):
        value = float(m.group(1).replace(",", "."))
        if not (0 < value <= MAX_PAUSE_S):
            raise PauseError(f"[pause:{m.group(1)}] phải lớn hơn 0 và không quá {MAX_PAUSE_S:g} giây")
        chunk = text[pos : m.start()]
        if chunk.strip():
            parts.append(("text", chunk))
        parts.append(("pause", value))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        parts.append(("text", tail))
    return parts


def strip_pauses(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", _PAUSE_RE.sub(" ", text)).strip()
