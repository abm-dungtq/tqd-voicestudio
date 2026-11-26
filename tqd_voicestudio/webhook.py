"""Signed webhook delivery (HMAC-SHA256 over the raw body, 3 attempts with backoff)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence

BACKOFF_S = (1.0, 4.0, 16.0)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(url: str, body: bytes, headers: Dict[str, str], timeout: float) -> int:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def deliver(
    url: str,
    payload: dict,
    secret: str,
    attempts: int = 3,
    backoff: Sequence[float] = BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
    post: Optional[Callable[[str, bytes, Dict[str, str], float], int]] = None,
    timeout: float = 10.0,
) -> List[dict]:
    """POST ``payload`` as JSON; retry on non-2xx/connection errors. Returns attempt records."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-TQD-Signature": sign(secret, body),
        "User-Agent": "tqd-voicestudio",
    }
    sender = post or _post
    records: List[dict] = []
    for n in range(1, attempts + 1):
        rec = {"attempt": n, "ts": time.time(), "status": None, "error": None}
        try:
            rec["status"] = int(sender(url, body, headers, timeout))
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        records.append(rec)
        if rec["status"] is not None and 200 <= rec["status"] < 300:
            break
        if n < attempts:
            sleep(backoff[min(n - 1, len(backoff) - 1)])
    return records
