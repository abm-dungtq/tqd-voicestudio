"""Background job queue: segment planning, resumable rendering, concatenation, webhook."""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
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
log = logging.getLogger("tqd.jobs")
TERMINAL_STATUSES = ("done", "failed", "canceled")


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
        jobs_max=100,
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
        self.jobs_max = jobs_max
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._cancel: set = set()
        self._active: set = set()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        for row in store.jobs_with_status("running"):
            store.update_job(row["id"], status="queued")
        for row in store.jobs_with_status("queued"):
            self._q.put(row["id"])
        self.prune_jobs()
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
        if row["status"] in ("queued", "running"):
            # The in-memory set is only a fast worker hint; the conditional SQL update
            # is authoritative. Add it only when cancellation actually wins so a late
            # cancel cannot retroactively poison a job that has already completed.
            if self.store.cancel_job(jid):
                self._cancel.add(jid)
                self._notify(jid)
            # Pruning is deliberately not done here: it is worker/startup-only so a
            # cancel call can never synchronously delete rows or files.
        current = self.store.get_job(jid)
        return current["status"] if current else None

    def _active_add(self, jid: str) -> None:
        with self._state_lock:
            self._active.add(jid)

    def _active_discard(self, jid: str) -> None:
        with self._state_lock:
            self._active.discard(jid)

    def _active_ids(self) -> set:
        with self._state_lock:
            return set(self._active)

    def _is_canceled(self, jid: str) -> bool:
        current = self.store.get_job(jid) or {}
        return bool(
            jid in self._cancel
            or current.get("cancel_requested")
            or current.get("status") == "canceled"
        )

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
            self._active_add(jid)
            try:
                self._run(jid)
            except Exception as exc:
                # Never strand a job because an infrastructure/store exception escaped
                # _run. In particular, failures before the normal failure handler can
                # otherwise leave the row permanently queued/running. The update is
                # still conditional on a live state so it cannot overwrite cancellation.
                logging.exception("job worker failed while processing %s", jid)
                try:
                    self.store._exec(
                        "UPDATE jobs SET status='failed', error=?, finished_at=? "
                        "WHERE id=? AND status IN ('queued','running')",
                        (f"{type(exc).__name__}: {exc}", time.time(), jid),
                    )
                except Exception:
                    logging.exception("could not mark escaped job failure for %s", jid)
                self._finish_safely(jid)
            finally:
                self._active_discard(jid)

    def _finish_safely(self, jid: str) -> None:
        try:
            self._finish(jid)
        except Exception:
            logging.exception("job finish housekeeping failed for %s", jid)

    def _run(self, jid: str) -> None:
        row = self.store.get_job(jid)
        if not row or row["status"] not in ("queued", "running"):
            return
        if not self.store.claim_job(jid):
            # Lost the atomic claim race (commonly because cancel won first).
            self._cancel.discard(jid)
            return
        request = json.loads(row["request_json"])
        plan = json.loads(row["plan_json"])
        try:
            (self.jobs_dir / jid).mkdir(parents=True, exist_ok=True)
            pieces: List[np.ndarray] = []
            segments: List[dict] = []
            pauses: Pauses = []
            offset = 0
            for idx, seg in enumerate(plan):
                if self._is_canceled(jid):
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
            if self._is_canceled(jid):
                raise JobCanceled()
            combined = np.concatenate(pieces) if pieces else np.zeros(NATIVE_SR // 10, dtype=np.float32)
            hid = self.assemble(request, combined, segments, pauses, bool(row["subtitles"]))
            # Cancellation can arrive while assemble() is doing finalization/encoding.
            # Because assemble() owns output creation, the history row may already exist;
            # remove it immediately rather than leaving an orphaned output behind.
            if self._is_canceled(jid):
                self._discard_assembled_history(hid)
                raise JobCanceled()
            if not self.store.complete_job(jid, hid, time.time()):
                # Cancellation won the atomic completion race after assembly.
                self._discard_assembled_history(hid)
                raise JobCanceled()
        except JobCanceled:
            self.store.update_job(jid, status="canceled", finished_at=time.time())
        except Exception as exc:
            self.store.fail_job(jid, f"{type(exc).__name__}: {exc}", time.time())
        self._finish(jid)

    def _discard_assembled_history(self, hid: Optional[str]) -> None:
        if not hid:
            return
        try:
            self.store.delete_history(hid)
        except Exception:
            log.exception("could not discard assembled output %s for canceled job", hid)

    def _finish(self, jid: str) -> None:
        row = self.store.get_job(jid)
        if not row or row["status"] not in TERMINAL_STATUSES:
            return
        if row["status"] in ("done", "canceled"):
            self._cleanup_segment_files(jid)
        self._cancel.discard(jid)
        self.prune_jobs()
        self._notify(jid)

    def _cleanup_segment_files(self, jid: str) -> None:
        d = self.jobs_dir / jid
        if not d.is_dir():
            return
        for pattern in ("seg-*.wav", "seg-*.json"):
            for path in d.glob(pattern):
                path.unlink(missing_ok=True)
        try:
            d.rmdir()  # only succeeds when nothing but segment artifacts was present
        except OSError:
            pass

    def _jobs_limit(self) -> int:
        value = self.jobs_max() if callable(self.jobs_max) else self.jobs_max
        return max(0, int(value))

    def prune_jobs(self) -> int:
        """Keep the most recently finished ``jobs_max`` terminal jobs.

        Active worker jobs count toward retention but are never deleted while claimed.
        The directory is removed before the row: when removal fails, keeping the row
        lets the next startup/worker prune pass retry the cleanup.
        """
        active = self._active_ids()
        terminal = self.store.terminal_jobs_by_finish()
        removed = 0
        for row in terminal[self._jobs_limit():]:
            jid = row["id"]
            if jid in active:
                continue
            job_dir = self.jobs_dir / jid
            try:
                if job_dir.exists():
                    shutil.rmtree(job_dir)
            except Exception as exc:
                log.warning("could not remove job directory %s; keeping job row for retry: %s", job_dir, exc)
                continue
            if self.store.delete_job(jid):
                removed += 1
        return removed

    def _notify(self, jid: str) -> None:
        row = self.store.get_job(jid)
        if not row or not row["webhook_url"] or row["status"] not in TERMINAL_STATUSES:
            return
        if not self.store.claim_notification(jid):
            return
        payload = {"event": f"job.{row['status']}", "job": self.view(row)}
        url = row["webhook_url"]
        thread = threading.Thread(
            target=self._deliver_webhook,
            args=(jid, url, payload),
            name=f"tqd-webhook-{jid}",
            daemon=True,
        )
        thread.start()

    def _deliver_webhook(self, jid: str, url: str, payload: dict) -> None:
        try:
            attempts = webhook_mod.deliver(
                url, payload, self.webhook_secret(),
                backoff=self.backoff, sleep=self.sleep, post=self.post,
            )
            self.store.update_job(jid, webhook_attempts=json.dumps(attempts))
        except Exception:
            logging.exception("webhook delivery failed for job %s", jid)
