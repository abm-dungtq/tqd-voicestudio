"""SQLite persistence for jobs, history, ratings and music (file blobs live beside the DB)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
  id TEXT PRIMARY KEY, created_at REAL, kind TEXT, cache_key TEXT, text TEXT, voice TEXT,
  model TEXT, response_format TEXT, sample_rate INTEGER, duration_s REAL, audio_path TEXT,
  media_type TEXT, segments_json TEXT, srt TEXT, vtt TEXT, params_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_key ON history(cache_key, created_at);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, status TEXT, request_json TEXT, plan_json TEXT, progress REAL DEFAULT 0,
  segments_done INTEGER DEFAULT 0, segments_total INTEGER DEFAULT 0, error TEXT,
  created_at REAL, finished_at REAL, history_id TEXT, webhook_url TEXT, subtitles INTEGER DEFAULT 1,
  webhook_attempts TEXT DEFAULT '[]', cancel_requested INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ratings (
  id TEXT PRIMARY KEY, created_at REAL, history_id TEXT, job_id TEXT, segment_index INTEGER,
  rating INTEGER, note TEXT, text TEXT, voice TEXT, model TEXT
);
CREATE TABLE IF NOT EXISTS music (
  id TEXT PRIMARY KEY, name TEXT, duration REAL, path TEXT, created_at REAL
);
"""


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # ---- history ----
    def add_history(self, **f: Any) -> str:
        hid = f.pop("id", None) or new_id()
        row = {
            "id": hid, "created_at": time.time(), "kind": "speech", "cache_key": "", "text": "",
            "voice": "", "model": "", "response_format": "wav", "sample_rate": 0, "duration_s": 0.0,
            "audio_path": "", "media_type": "", "segments_json": "[]", "srt": None, "vtt": None,
            "params_json": "{}",
        }
        row.update(f)
        cols = ",".join(row)
        self._exec(f"INSERT INTO history ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return hid

    def get_history(self, hid: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM history WHERE id=?", (hid,))

    def find_cached(self, key: str) -> Optional[Dict[str, Any]]:
        rows = self._all("SELECT * FROM history WHERE cache_key=? ORDER BY created_at DESC LIMIT 1", (key,))
        for row in rows:
            if row["audio_path"] and Path(row["audio_path"]).is_file():
                return row
        return None

    def list_history(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT id,created_at,kind,text,voice,model,response_format,sample_rate,duration_s,media_type "
            "FROM history ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )

    def delete_history(self, hid: str) -> bool:
        row = self.get_history(hid)
        if not row:
            return False
        self._exec("DELETE FROM history WHERE id=?", (hid,))
        if row["audio_path"]:
            Path(row["audio_path"]).unlink(missing_ok=True)
        return True

    def prune_history(self, keep: int) -> int:
        rows = self._all("SELECT id FROM history ORDER BY created_at DESC, rowid DESC")
        removed = 0
        for row in rows[max(keep, 0):]:
            if self.delete_history(row["id"]):
                removed += 1
        return removed

    # ---- jobs ----
    def add_job(self, request: dict, plan: List[dict], webhook_url: Optional[str], subtitles: bool) -> str:
        jid = new_id()
        self._exec(
            "INSERT INTO jobs (id,status,request_json,plan_json,segments_total,created_at,webhook_url,subtitles) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (jid, "queued", json.dumps(request, ensure_ascii=False), json.dumps(plan, ensure_ascii=False),
             len(plan), time.time(), webhook_url, int(subtitles)),
        )
        return jid

    def get_job(self, jid: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM jobs WHERE id=?", (jid,))

    def list_jobs(self) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC")

    def update_job(self, jid: str, **f: Any) -> None:
        if not f:
            return
        sets = ",".join(f"{k}=?" for k in f)
        self._exec(f"UPDATE jobs SET {sets} WHERE id=?", (*f.values(), jid))

    def jobs_with_status(self, *statuses: str) -> List[Dict[str, Any]]:
        q = ",".join("?" * len(statuses))
        return self._all(f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY created_at, rowid", tuple(statuses))

    # ---- ratings ----
    def add_rating(self, **f: Any) -> str:
        rid = new_id()
        row = {"id": rid, "created_at": time.time(), "history_id": None, "job_id": None,
               "segment_index": None, "rating": 0, "note": None, "text": "", "voice": "", "model": ""}
        row.update(f)
        self._exec(f"INSERT INTO ratings ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        return rid

    def list_ratings(self) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM ratings ORDER BY created_at, rowid")

    # ---- music ----
    def add_music(self, name: str, duration: float, path: str) -> str:
        mid = new_id()
        self._exec("INSERT INTO music (id,name,duration,path,created_at) VALUES (?,?,?,?,?)",
                   (mid, name, duration, path, time.time()))
        return mid

    def get_music(self, mid: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM music WHERE id=?", (mid,))

    def list_music(self) -> List[Dict[str, Any]]:
        return self._all("SELECT id,name,duration,created_at FROM music ORDER BY created_at, rowid")

    def delete_music(self, mid: str) -> bool:
        row = self.get_music(mid)
        if not row:
            return False
        self._exec("DELETE FROM music WHERE id=?", (mid,))
        Path(row["path"]).unlink(missing_ok=True)
        return True
