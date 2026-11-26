"""Role lines (``Tên vai: lời``) mapped to ``[voice=...]`` tags through a cast."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_PUNCT = set(".,;:!?\"'()[]{}<>/|@#$%^&*=+~`-_") | {chr(92)}
_ROLE_LINE = re.compile(r"^\s*([^:\n]{1,60}?)\s*:\s*(.*)$")


def _role_of(line: str) -> Optional[Tuple[str, str]]:
    m = _ROLE_LINE.match(line)
    if not m:
        return None
    name, rest = m.group(1).strip(), m.group(2)
    words = name.split()
    if not (1 <= len(words) <= 4) or any(ch in _PUNCT for ch in name):
        return None
    if name.lower() in ("http", "https"):
        return None
    return name, rest


def parse_roles(text: str) -> List[str]:
    """Role names in order of first appearance."""
    roles: List[str] = []
    for line in text.splitlines():
        r = _role_of(line)
        if r and r[0] not in roles:
            roles.append(r[0])
    return roles


def apply_cast(text: str, cast: Dict[str, str]) -> str:
    """Rewrite role lines to ``[voice=cast[role]] lời``.

    Lines without a role prefix continue the previous role (before any role, text is untouched).
    Raises ``KeyError(missing_roles)`` listing roles absent from ``cast``.
    """
    roles = parse_roles(text)
    missing = [r for r in roles if r not in cast]
    if missing:
        raise KeyError(missing)
    out: List[str] = []
    current: Optional[str] = None
    for line in text.splitlines():
        r = _role_of(line)
        if r:
            current = r[0]
            out.append(f"[voice={cast[current]}] {r[1]}".rstrip())
        elif line.strip() == "":
            out.append(line)
        elif current is not None:
            out.append(f"[voice={cast[current]}] {line.strip()}")
        else:
            out.append(line)
    return "\n".join(out)
