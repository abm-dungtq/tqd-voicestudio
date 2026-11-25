"""Hot-reloadable pronunciation lexicon.

JSON file ``{"entries": {"COVID": "cô vít", ...}}``. Whole-word, case-sensitive
replacement, longest keys first. The file is re-read automatically whenever its
mtime changes. Path from ``VIZIPVOICE_LEXICON_PATH`` (default:
``pronunciation_lexicon.json`` next to the API server).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

DEFAULT_LEXICON_NAME = "pronunciation_lexicon.json"


def default_path() -> Path:
    env = os.environ.get("VIZIPVOICE_LEXICON_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / DEFAULT_LEXICON_NAME


class PronunciationLexicon:
    def __init__(self, path: Optional[os.PathLike] = None) -> None:
        self.path = Path(path) if path else default_path()
        self._mtime: Optional[float] = None
        self._entries: Dict[str, str] = {}
        self._compiled: list = []
        self._load()

    def _load(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            self._mtime = None
            self._entries = {}
            self._compiled = []
            return
        self._mtime = stat.st_mtime
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("entries", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            entries = {}
        self._entries = {str(k): str(v) for k, v in entries.items()}
        # Longest keys first so e.g. "COVID-19" wins over "COVID".
        self._compiled = [
            (re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)"), v)
            for k, v in sorted(self._entries.items(), key=lambda kv: -len(kv[0]))
        ]

    def _maybe_reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self._mtime:
            self._load()

    def apply(self, text: str) -> str:
        self._maybe_reload()
        for rx, repl in self._compiled:
            text = rx.sub(repl, text)
        return text

    def get(self) -> Dict[str, str]:
        self._maybe_reload()
        return dict(self._entries)

    def replace(self, entries: Dict[str, str]) -> None:
        """Atomically rewrite the lexicon file and refresh the cache."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"entries": {str(k): str(v) for k, v in entries.items()}}
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
        self._load()


_default: Optional[PronunciationLexicon] = None


def _default_lexicon() -> PronunciationLexicon:
    global _default
    if _default is None:
        _default = PronunciationLexicon()
    return _default


def get() -> Dict[str, str]:
    return _default_lexicon().get()


def replace(entries: Dict[str, str]) -> None:
    _default_lexicon().replace(entries)


def apply(text: str) -> str:
    return _default_lexicon().apply(text)
