"""Second-round regression tests for review-verified job/cache/decode races."""

import io
import json
import sqlite3
import threading
import time

import numpy as np
import pytest
import soundfile as sf
import torch
from fastapi.testclient import TestClient

import vizipvoice_openai_server as srv
from tqd_voicestudio import jobs as tqd_jobs
from tqd_voicestudio.store import Store
from vizipvoice_addons import audio_formats, voice_meta

SR = 24000


def tone(n=100):
    return np.full(n, 0.1, dtype=np.float32)


def wav_bytes(wav, sr=SR):
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return buf.getvalue()


def make_manager(tmp_path, render, assemble=None, **kw):
    store = Store(tmp_path / "studio.db")

    def default_assemble(request, wav, segments, pauses, subs):
        return "hist"

    mgr = tqd_jobs.JobManager(
        store,
        tmp_path / "jobs",
        render=render,
        assemble=assemble or default_assemble,
        webhook_secret=lambda: "secret",
        backoff=(0, 0, 0),
        start=False,
        **kw,
    )
    return store, mgr


def wait_until(predicate, timeout=5.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---- 1. pruning order/timing/active-job guard ---------------------------------
def test_cancel_does_not_prune_synchronously(tmp_path):
    store, mgr = make_manager(tmp_path, lambda r, s: (tone(), [], []), jobs_max=0)
    jid = mgr.submit({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)

    assert mgr.cancel(jid) == "canceled"
    # With jobs_max=0 any synchronous cancel-time pruning would delete this row.
    assert store.get_job(jid) is not None
    store.close()


def test_prune_uses_finish_time_and_never_removes_active_recent_job(tmp_path):
    deliveries = []
    delivery_lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()

    def post(url, body, headers, timeout):
        with delivery_lock:
            deliveries.append(json.loads(body.decode("utf-8")))
        return 200

    def render(request, seg):
        started.set()
        release.wait(5)
        return tone(), [], []

    store, mgr = make_manager(tmp_path, render, jobs_max=1, post=post)
    job_a = mgr.submit(
        {"input": "a"}, [{"voice": "A", "text": "a", "join": None}], "http://hook.local/a", True
    )
    mgr.start()
    assert started.wait(5)

    job_b = mgr.submit({"input": "b"}, [{"voice": "A", "text": "b", "join": None}], None, True)
    assert mgr.cancel(job_b) == "canceled"
    assert store.get_job(job_b) is not None, "cancel itself must not prune"

    release.set()
    assert wait_until(lambda: store.get_job(job_a) and store.get_job(job_a)["status"] == "done")
    assert wait_until(lambda: len(deliveries) == 1, timeout=3)
    # Give the worker a beat to run its post-finish prune pass.
    assert wait_until(lambda: store.get_job(job_b) is None, timeout=3)
    mgr.stop()

    row_a = store.get_job(job_a)
    assert row_a is not None, "the most recently finished job must not be pruned"
    assert row_a["status"] == "done"
    assert deliveries[0]["event"] == "job.done"
    assert deliveries[0]["job"]["id"] == job_a
    store.close()


def test_failed_job_directory_removal_leaves_row_for_retry(tmp_path, monkeypatch):
    store, mgr = make_manager(tmp_path, lambda r, s: (tone(), [], []), jobs_max=0)
    jid = store.add_job({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
    store.update_job(jid, status="done", finished_at=time.time())
    job_dir = tmp_path / "jobs" / jid
    job_dir.mkdir(parents=True)
    (job_dir / "leftover.bin").write_bytes(b"x")

    def broken_rmtree(path, *args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(tqd_jobs.shutil, "rmtree", broken_rmtree)
    assert mgr.prune_jobs() == 0
    assert store.get_job(jid) is not None
    assert job_dir.exists()
    store.close()


# ---- 2. failure cannot overwrite cancellation --------------------------------
def test_cancel_accepted_while_render_blocked_survives_render_exception(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def render(request, seg):
        started.set()
        release.wait(5)
        raise RuntimeError("backend exploded after cancellation")

    store, mgr = make_manager(tmp_path, render)
    jid = mgr.submit({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
    mgr.start()
    assert started.wait(5)
    assert mgr.cancel(jid) == "canceled"
    release.set()

    time.sleep(0.25)  # allow any erroneous late failed transition to appear
    mgr.stop()
    row = store.get_job(jid)
    assert row["status"] == "canceled"
    assert row["error"] is None
    store.close()


# ---- 3. cancellation during assembly discards assembled output ----------------
def test_cancel_during_assemble_deletes_history_row_and_audio(tmp_path):
    assemble_started = threading.Event()
    release_assemble = threading.Event()
    created = {}

    def render(request, seg):
        return tone(), [], []

    store = None

    def assemble(request, wav, segments, pauses, subs):
        assemble_started.set()
        release_assemble.wait(5)
        path = tmp_path / "orphan-output.wav"
        path.write_bytes(b"assembled")
        hid = store.add_history(kind="job", audio_path=str(path), text=request["input"])
        created.update(hid=hid, path=path)
        return hid

    store, mgr = make_manager(tmp_path, render, assemble=assemble)
    jid = mgr.submit({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
    mgr.start()
    assert assemble_started.wait(5)
    assert mgr.cancel(jid) == "canceled"
    release_assemble.set()

    assert wait_until(lambda: store.get_job(jid)["status"] == "canceled" and "hid" in created)
    assert wait_until(lambda: store.get_history(created["hid"]) is None)
    mgr.stop()
    assert not created["path"].exists()
    assert store.get_job(jid)["history_id"] is None
    store.close()


# ---- 4. exactly-once webhook notification ------------------------------------
def test_cancel_running_job_delivers_exactly_one_canceled_webhook(tmp_path):
    started = threading.Event()
    release = threading.Event()
    deliveries = []
    delivery_lock = threading.Lock()

    def render(request, seg):
        started.set()
        release.wait(5)
        return tone(), [], []

    def post(url, body, headers, timeout):
        with delivery_lock:
            deliveries.append(json.loads(body.decode("utf-8")))
        return 200

    store, mgr = make_manager(tmp_path, render, post=post)
    jid = mgr.submit(
        {"input": "x"}, [{"voice": "A", "text": "a", "join": None}], "http://hook.local/cancel", True
    )
    mgr.start()
    assert started.wait(5)
    assert mgr.cancel(jid) == "canceled"
    release.set()

    assert wait_until(lambda: len(json.loads(store.get_job(jid)["webhook_attempts"])) == 1, timeout=3)
    time.sleep(0.2)  # worker finish has now had its chance to duplicate the notification
    mgr.stop()
    assert len(deliveries) == 1
    assert deliveries[0]["event"] == "job.canceled"
    assert deliveries[0]["job"]["id"] == jid
    row = store.get_job(jid)
    assert row["notified"] == 1
    attempts = json.loads(row["webhook_attempts"])
    assert len(attempts) == 1 and attempts[0]["status"] == 200
    store.close()


def test_existing_database_is_migrated_with_notified_column(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, request_json TEXT, plan_json TEXT, "
        "progress REAL DEFAULT 0, segments_done INTEGER DEFAULT 0, segments_total INTEGER DEFAULT 0, "
        "error TEXT, created_at REAL, finished_at REAL, history_id TEXT, webhook_url TEXT, "
        "subtitles INTEGER DEFAULT 1, webhook_attempts TEXT DEFAULT '[]', cancel_requested INTEGER DEFAULT 0)"
    )
    con.execute("INSERT INTO jobs (id,status,request_json,plan_json,created_at) VALUES ('j','done','{}','[]',1)")
    con.commit()
    con.close()

    store = Store(db)
    row = store.get_job("j")
    assert row["notified"] == 0
    assert store.claim_notification("j") is True
    assert store.claim_notification("j") is False
    store.close()


# ---- 5. enrollment-generation voice fingerprint/cache invalidation ------------
class EnrollingFakeEngine:
    """Synthetic synthesizer backed by the real ViZipVoiceEngine.enroll_voice code."""

    max_streams = 1
    active = 0
    waiting = 0

    def __init__(self):
        self.real = object.__new__(srv.ViZipVoiceEngine)
        self.real.voices = {}
        self.real.alias_map = {}
        self.real._ensure_cached_prompt = lambda entry: None
        self.calls = []

    @property
    def voices(self):
        return self.real.voices

    def acquire(self):
        pass

    def release(self):
        pass

    def resolve_voice(self, name):
        return self.real.resolve_voice(name)

    def enroll_voice(self, *args, **kwargs):
        return self.real.enroll_voice(*args, **kwargs)

    def synthesize_tensor(self, text, entry, speed=None, style="auto", quality_check=None,
                          segments_out=None, qc_out=None):
        self.calls.append((entry.name, text))
        wav = np.full(int(0.5 * SR), 0.05, dtype=np.float32)
        if segments_out is not None:
            segments_out.append({"start": 0.0, "end": 0.5, "text": text, "voice": entry.name})
        return torch.from_numpy(wav).unsqueeze(0)


def test_same_length_reenrollment_changes_generation_and_invalidates_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIZIPVOICE_LEXICON_PATH", str(tmp_path / "lex.json"))
    monkeypatch.setattr(srv, "API_KEY", "")
    monkeypatch.setattr(srv, "CUSTOM_VOICES_DIR", tmp_path / "voices")
    monkeypatch.setattr(srv, "LIBRARY_DIR", None)
    monkeypatch.setattr(srv, "_STORE", None)
    eng = EnrollingFakeEngine()
    monkeypatch.setattr(srv, "engine", eng)
    client = TestClient(srv.app)

    def enroll(freq, force=False):
        t = np.arange(SR) / SR
        wav = (0.08 * np.sin(2 * np.pi * freq * t)).astype(np.float32)  # identical sample count
        data = {"name": "GenVoice", "consent": "true", "prompt_text": "lời nhắc kiểm thử"}
        if force:
            data["force"] = "true"
        response = client.post(
            "/v1/voices",
            files={"file": ("ref.wav", wav_bytes(wav), "audio/wav")},
            data=data,
        )
        assert response.status_code == 201, response.text

    def speech():
        return client.post(
            "/v1/audio/speech",
            json={"input": "Câu kiểm tra vân tay giọng", "voice": "GenVoice",
                  "response_format": "wav", "sample_rate": SR},
        )

    enroll(210)
    sidecar = voice_meta.read_sidecar(tmp_path / "voices" / "GenVoice.json")
    first_gen = sidecar.get("gen")
    assert first_gen
    fp1 = srv._voice_content_fingerprint(eng.resolve_voice("GenVoice"))

    assert speech().headers["x-tqd-cache"] == "miss"
    assert speech().headers["x-tqd-cache"] == "hit"
    assert len(eng.calls) == 1, "repeated renders with a stable generation must hit cache"
    assert srv._voice_content_fingerprint(eng.resolve_voice("GenVoice")) == fp1

    enroll(310, force=True)
    second_gen = voice_meta.read_sidecar(tmp_path / "voices" / "GenVoice.json").get("gen")
    fp2 = srv._voice_content_fingerprint(eng.resolve_voice("GenVoice"))
    assert second_gen and second_gen != first_gen
    assert fp2 != fp1

    assert speech().headers["x-tqd-cache"] == "miss"
    assert len(eng.calls) == 2, "same-length replacement audio must miss the cache"
    store = srv.get_store()
    store.close()
    monkeypatch.setattr(srv, "_STORE", None)


# ---- 6. decode bomb prevention --------------------------------------------------
def test_soundfile_duration_rejected_before_decode_allocates_samples(monkeypatch):
    sr = 1000
    long_wav = wav_bytes(np.zeros(601 * sr, dtype=np.float32), sr=sr)

    def forbidden_read(*args, **kwargs):
        raise AssertionError("sf.read was reached; duration was not rejected from metadata")

    monkeypatch.setattr(sf, "read", forbidden_read)
    with pytest.raises(ValueError, match="10 phút"):
        audio_formats.decode_audio(long_wav)


def test_ffprobe_duration_rejects_before_ffmpeg_decode(monkeypatch, tmp_path):
    monkeypatch.setattr(sf, "info", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unknown container")))
    monkeypatch.setattr(sf, "read", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unknown container")))
    monkeypatch.setattr(audio_formats.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(audio_formats, "_probe_duration_ffprobe", lambda path: 601.0)

    def forbidden_decode(*args, **kwargs):
        raise AssertionError("ffmpeg decode started despite over-duration probe")

    monkeypatch.setattr(audio_formats, "_decode_ffmpeg_capped", forbidden_decode)
    with pytest.raises(ValueError, match="10 phút"):
        audio_formats.decode_audio(b"opaque-container")
