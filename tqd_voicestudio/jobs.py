"""Background job queue: segment planning, resumable rendering, concatenation, webhook."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
import soundfile as sf

from tqd_voicestudio import webhook as webhook_mod
from tqd_voicestudio.store import Store

NATIVE_SR = 24000
JOIN_MS = {"voice": 250, "paragraph": 480, "sentence": 190}
MAX_PARAGRAPH_WORDS = 400
_SENT_END = re.compile(r"(?<=[.!?…])\s+")

Pauses = List[Tuple[int, int]]


def _split_long_paragraph(text: str, max_words: int = MAX_PARAGRAPH_WORDS) -> List[str]:
    if len(text.split()) <= max_words:
        return [text]
    chunks: List[str] = []
    cur: List[str] = []
    count = 0
    for sent in _SENT_END.split(text):
        w = len(sent.split())
        if cur and count + w > max_words:
            chunks.append(" ".join(cur))
            cur, count = [], 0
        cur.append(sent)
        count += w
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def plan_segments(
    text: str,
    default_voice: str,
    split_runs: Callable[[str, str], List[Tuple[str, str]]],
) -> List[dict]:
    """Segments split at voice-run and paragraph (blank line) boundaries.

    Each segment is ``{"voice", "text", "join"}``; ``join`` names the silence that precedes it
    (``voice`` 250 ms, ``paragraph`` 480 ms, ``sentence`` 190 ms; ``None`` for the first).
    Paragraphs over 400 words are split at sentence ends.
    """
    plan: List[dict] = []
    for voice, run_text in split_runs(text, default_voice):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", run_text) if p.strip()]
        for p_idx, para in enumerate(paragraphs):
            for k, chunk in enumerate(_split_long_paragraph(para)):
                if not plan:
                    join = None
                elif p_idx == 0 and k == 0:
                    join = "voice"
                elif k == 0:
                    join = "paragraph"
                else:
                    join = "sentence"
                plan.append({"voice": voice, "text": chunk, "join": join})
    return plan


class JobCanceled(Exception):
    pass


class JobManager:
    """One worker thread, FIFO.

    ``render(request, segment) -> (wav float32 [n] @24k, segments, pause_ranges)`` renders one segment.
    ``assemble(request, wav, segments, pauses, subtitles) -> history_id`` post-processes, encodes and
    stores the finished job output.
    """

    def __init__(
        self,
        store: Store,
        jobs_dir: Path,
        render: Callable[[dict, dict], Tuple[np.ndarray, List[dict], Pauses]],
        assemble: Callable[[dict, np.ndarray, List[dict], Pauses, bool], str],
        webhook_secret: Callable[[], str],
        backoff=webhook_mod.BACKOFF_S,
        sleep: Callable[[float], None] = time.sleep,
        post=None,
        start: bool = True,
    ):
        self.store = store
        self.jobs_dir = Path(jobs_dir)
        self.render = render
        self.assemble = assemble
        self.webhook_secret = webhook_secret
        self.backoff = backoff
        self.sleep = sleep
        self.post = post
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._cancel: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        for row in store.jobs_with_status("running"):
            store.update_job(row["id"], status="queued")
        for row in store.jobs_with_status("queued"):
            self._q.put(row["id"])
        if start:
            self.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tqd-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def submit(self, request: dict, plan: List[dict], webhook_url: Optional[str], subtitles: bool) -> str:
        jid = self.store.add_job(request, plan, webhook_url, subtitles)
        self._q.put(jid)
        return jid

    def cancel(self, jid: str) -> Optional[str]:
        row = self.store.get_job(jid)
        if not row:
            return None
        if row["status"] == "queued":
            self.store.update_job(jid, status="canceled", finished_at=time.time())
            self._notify(jid)
        elif row["status"] == "running":
            self._cancel.add(jid)
            self.store.update_job(jid, cancel_requested=1)
        return self.store.get_job(jid)["status"]

    def view(self, row: dict) -> dict:
        jid = row["id"]
        done = row["status"] == "done"
        total = row["segments_total"]
        return {
            "id": jid,
            "status": row["status"],
            "progress": 1.0 if done else (round(row["segments_done"] / total, 3) if total else 0.0),
            "segments_done": row["segments_done"],
            "segments_total": total,
            "error": row["error"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "audio_url": f"/v1/jobs/{jid}/audio" if done else None,
            "srt_url": f"/v1/jobs/{jid}/subtitles.srt" if done and row["subtitles"] else None,
            "vtt_url": f"/v1/jobs/{jid}/subtitles.vtt" if done and row["subtitles"] else None,
            "history_id": row["history_id"],
            "webhook_attempts": json.loads(row["webhook_attempts"] or "[]"),
        }

    def seg_paths(self, jid: str, idx: int) -> Tuple[Path, Path]:
        d = self.jobs_dir / jid
        return d / f"seg-{idx:04d}.wav", d / f"seg-{idx:04d}.json"

    def _loop(self) -> None:
        while not self._stop.is_set():
            jid = self._q.get()
            if jid is None:
                break
            try:
                self._run(jid)
            except Exception:
                pass

    def _run(self, jid: str) -> None:
        row = self.store.get_job(jid)
        if not row or row["status"] not in ("queued", "running"):
            return
        self.store.update_job(jid, status="running")
        request = json.loads(row["request_json"])
        plan = json.loads(row["plan_json"])
        try:
            (self.jobs_dir / jid).mkdir(parents=True, exist_ok=True)
            pieces: List[np.ndarray] = []
            segments: List[dict] = []
            pauses: Pauses = []
            offset = 0
            for idx, seg in enumerate(plan):
                current = self.store.get_job(jid) or {}
                if jid in self._cancel or current.get("cancel_requested"):
                    raise JobCanceled()
                wav_p, meta_p = self.seg_paths(jid, idx)
                if wav_p.is_file() and meta_p.is_file():
                    wav, _sr = sf.read(str(wav_p), dtype="float32")
                    meta = json.loads(meta_p.read_text(encoding="utf-8"))
                else:
                    wav, segs, seg_pauses = self.render(request, seg)
                    wav = np.asarray(wav, dtype=np.float32)
                    sf.write(str(wav_p), wav, NATIVE_SR, subtype="FLOAT")
                    meta = {"segments": segs, "pauses": [list(p) for p in seg_pauses], "samples": int(len(wav))}
                    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                self.store.update_job(jid, segments_done=idx + 1)
                join_key = seg.get("join")
                if join_key:
                    n = int(NATIVE_SR * JOIN_MS[join_key] / 1000)
                    pieces.append(np.zeros(n, dtype=np.float32))
                    offset += n
                for s in meta["segments"]:
                    segments.append({
                        **s,
                        "start": round(s["start"] + offset / NATIVE_SR, 3),
                        "end": round(s["end"] + offset / NATIVE_SR, 3),
                    })
                for a, b in meta["pauses"]:
                    pauses.append((a + offset, b + offset))
                pieces.append(np.asarray(wav, dtype=np.float32))
                offset += len(wav)
            combined = np.concatenate(pieces) if pieces else np.zeros(NATIVE_SR // 10, dtype=np.float32)
            hid = self.assemble(request, combined, segments, pauses, bool(row["subtitles"]))
            self.store.update_job(jid, status="done", history_id=hid, finished_at=time.time())
        except JobCanceled:
            self.store.update_job(jid, status="canceled", finished_at=time.time())
        except Exception as exc:
            self.store.update_job(jid, status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=time.time())
        self._notify(jid)

    def _notify(self, jid: str) -> None:
        row = self.store.get_job(jid)
        if not row or not row["webhook_url"] or row["status"] not in ("done", "failed", "canceled"):
            return
        payload = {"event": f"job.{row['status']}", "job": self.view(row)}
        attempts = webhook_mod.deliver(
            row["webhook_url"], payload, self.webhook_secret(),
            backoff=self.backoff, sleep=self.sleep, post=self.post,
        )
        self.store.update_job(jid, webhook_attempts=json.dumps(attempts))
