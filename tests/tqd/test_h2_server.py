import base64
import hashlib
import hmac
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
import torch
from fastapi.testclient import TestClient

import vizipvoice_openai_server as srv
from tqd_voicestudio import config, jobs as tqd_jobs, store as tqd_store

SR = 24000


def _tone(seconds, freq, amp=0.1):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class FakeEngine:
    max_streams = 1
    active = 0
    waiting = 0

    def __init__(self):
        self.voices = {
            "Alpha": SimpleNamespace(name="Alpha", kind="preset"),
            "Beta": SimpleNamespace(name="Beta", kind="preset"),
        }
        self.calls = []
        self.gate = None
        self.on_call = None

    def list_voices(self):
        return [{"id": v.name, "name": v.name, "kind": v.kind, "gender": None, "region": None} for v in self.voices.values()]

    def resolve_voice(self, name):
        if name in self.voices:
            return self.voices[name]
        raise KeyError(name)

    def acquire(self):
        pass

    def release(self):
        pass

    def synthesize_tensor(self, text, entry, speed=None, style="auto", quality_check=None,
                          segments_out=None, qc_out=None):
        self.calls.append((entry.name, text))
        if self.on_call:
            self.on_call(text)
        if self.gate is not None:
            self.gate.wait(10)
        freq = 200 + (int(hashlib.md5(entry.name.encode()).hexdigest(), 16) % 5) * 60
        if "SILENCE" in text:
            wav = np.concatenate([_tone(1.0, freq), np.zeros(int(2.0 * SR), np.float32), _tone(1.0, freq)])
        else:
            wav = _tone(max(0.5, 0.3 * len(text.split())), freq)
        if segments_out is not None:
            segments_out.append({"start": 0.0, "end": round(len(wav) / SR, 3), "text": text})
        return torch.from_numpy(wav).unsqueeze(0)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    monkeypatch.setenv("VIZIPVOICE_LEXICON_PATH", str(tmp_path / "lex.json"))
    for name in ("VIZIPVOICE_API_KEY",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(srv, "API_KEY", "")
    eng = FakeEngine()
    monkeypatch.setattr(srv, "engine", eng)
    monkeypatch.setattr(srv, "_STORE", None)
    monkeypatch.setattr(srv, "_JOBS", None)
    store = srv.get_store()
    mgr = tqd_jobs.JobManager(
        store, tmp_path / "jobs", render=srv._job_render, assemble=srv._job_assemble,
        webhook_secret=config.ensure_webhook_secret, backoff=(0, 0, 0),
    )
    monkeypatch.setattr(srv, "_JOBS", mgr)
    client = TestClient(srv.app)
    yield client, eng, tmp_path
    mgr.stop()
    store.close()


def speech(c, **body):
    payload = {"input": "Xin chào các bạn", "voice": "Alpha", "response_format": "wav", "sample_rate": 24000}
    payload.update(body)
    return c.post("/v1/audio/speech", json=payload)


def wait_job(c, jid, status=("done", "failed", "canceled"), timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/v1/jobs/{jid}").json()
        if j["status"] in status:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {j}")


# ---- cache + history ----
def test_cache_miss_hit_bypass(env):
    c, eng, _ = env
    r1 = speech(c)
    assert r1.status_code == 200 and r1.headers["x-tqd-cache"] == "miss"
    r2 = speech(c)
    assert r2.headers["x-tqd-cache"] == "hit" and r2.content == r1.content
    assert r2.headers["x-tqd-history-id"] == r1.headers["x-tqd-history-id"]
    assert len(eng.calls) == 1
    r3 = speech(c, cache=False)
    assert r3.headers["x-tqd-cache"] == "miss" and len(eng.calls) == 2
    assert speech(c, speed=1.2).headers["x-tqd-cache"] == "miss"


def test_cache_invalidated_by_lexicon_change(env):
    c, eng, _ = env
    speech(c)
    assert speech(c).headers["x-tqd-cache"] == "hit"
    assert c.put("/v1/lexicon", json={"entries": {"COVID": "cô vít"}}).status_code == 200
    assert speech(c).headers["x-tqd-cache"] == "miss"
    assert speech(c).headers["x-tqd-cache"] == "hit"


def test_history_list_audio_delete_and_prune(env):
    c, _, tmp = env
    ids = [speech(c, input=f"Câu số {i} ở đây").headers["x-tqd-history-id"] for i in range(3)]
    items = c.get("/v1/history").json()["data"]
    assert [i["id"] for i in items] == ids[::-1]
    assert c.get("/v1/history?limit=1&offset=1").json()["data"][0]["id"] == ids[1]
    audio = c.get(f"/v1/history/{ids[0]}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert c.delete(f"/v1/history/{ids[0]}").status_code == 200
    assert c.get(f"/v1/history/{ids[0]}/audio").status_code == 404
    assert c.delete(f"/v1/history/{ids[0]}").status_code == 404
    cfg = config.load()
    cfg.history_max = 2
    config.save(cfg)
    speech(c, input="Câu mới nhất để cắt tỉa")
    remaining = [i["id"] for i in c.get("/v1/history").json()["data"]]
    assert len(remaining) == 2 and ids[1] not in remaining
    assert len(list((tmp / "history").glob("*"))) == 2


# ---- ratings ----
def test_ratings_json_and_jsonl(env):
    c, _, _ = env
    hid = speech(c).headers["x-tqd-history-id"]
    assert c.post("/v1/ratings", json={"history_id": hid, "rating": 1, "note": "tốt"}).status_code == 201
    assert c.post("/v1/ratings", json={"history_id": hid, "segment_index": 0, "rating": -1}).status_code == 201
    assert c.post("/v1/ratings", json={"history_id": hid, "rating": 5}).status_code == 422
    assert c.post("/v1/ratings", json={"rating": 1}).status_code == 400
    assert c.post("/v1/ratings", json={"history_id": "nope", "rating": 1}).status_code == 404
    data = c.get("/v1/ratings").json()["data"]
    assert [r["rating"] for r in data] == [1, -1]
    assert data[0]["text"] == "Xin chào các bạn" and data[0]["voice"] == "Alpha" and data[0]["note"] == "tốt"
    lines = c.get("/v1/ratings?format=jsonl").text.strip().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["segment_index"] == 0


# ---- pauses / roles ----
def test_pause_exact_and_stripped_from_subtitles(env):
    c, _, _ = env
    r = c.post("/v1/audio/speech/timed", json={
        "input": "Xin chào các bạn [pause:2] tạm biệt nhé", "voice": "Alpha",
        "response_format": "wav", "sample_rate": 24000}).json()
    segs = r["segments"]
    assert len(segs) == 2 and "[pause" not in json.dumps(segs) and "[pause" not in r["srt"]
    assert abs((segs[1]["start"] - segs[0]["end"]) - 2.0) < 0.01


def test_pause_validation_400(env):
    c, _, _ = env
    assert speech(c, input="a b [pause:11] c d").status_code == 400
    assert speech(c, input="a b [pause:0] c d").status_code == 400


def test_roles_cast_and_unknown_role(env):
    c, eng, _ = env
    text = "Lan: Chào anh nhé.\nNam: Chào chị nhé."
    assert c.post("/v1/script/roles", json={"input": text}).json() == {"roles": ["Lan", "Nam"]}
    r = c.post("/v1/audio/speech/timed", json={
        "input": text, "voice": "Alpha", "cast": {"Lan": "Alpha", "Nam": "Beta"},
        "response_format": "wav", "sample_rate": 24000}).json()
    assert [s["voice"] for s in r["segments"]] == ["Alpha", "Beta"]
    bad = speech(c, input=text, cast={"Lan": "Alpha"})
    assert bad.status_code == 400 and "Nam" in bad.json()["detail"] and "Lan, Nam" in bad.json()["detail"]
    assert speech(c, input=text, cast={"Lan": "Alpha", "Nam": "Zzz"}).status_code == 400


# ---- post-processing ----
def decode(resp):
    wav, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
    return wav, sr


def test_normalize_hits_minus_16_lufs(env):
    import pyloudnorm

    c, _, _ = env
    r = speech(c, input="một hai ba bốn năm sáu bảy tám chín mười mười một mười hai", normalize=True)
    wav, sr = decode(r)
    lufs = pyloudnorm.Meter(sr).integrated_loudness(wav.astype(np.float64))
    assert abs(lufs + 16.0) < 0.5


def test_trim_silence_keeps_explicit_pause(env):
    c, _, _ = env
    plain = decode(speech(c, input="SILENCE một hai"))[0]
    trimmed = decode(speech(c, input="SILENCE một hai", trim_silence=True))[0]
    assert len(plain) - len(trimmed) > 1.2 * SR
    with_pause = decode(speech(c, input="một hai [pause:2] ba bốn", trim_silence=True))[0]
    without = decode(speech(c, input="một hai ba bốn", trim_silence=True))[0]
    assert abs((len(with_pause) - len(without)) - 2 * SR) < 0.1 * SR
    timed = c.post("/v1/audio/speech/timed", json={
        "input": "SILENCE một hai", "trim_silence": True, "voice": "Alpha", "response_format": "wav",
        "sample_rate": 24000}).json()
    assert timed["segments"][0]["end"] < len(plain) / SR - 1.0


def wav_bytes(wav, sr=SR):
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="WAV")
    return buf.getvalue()


def test_music_upload_list_delete_and_mix(env):
    c, _, _ = env
    r = c.post("/v1/music", files={"file": ("loop.wav", wav_bytes(_tone(1.0, 330)), "audio/wav")}, data={"name": "nhạc"})
    assert r.status_code == 201
    mid = r.json()["id"]
    assert r.json()["name"] == "nhạc" and abs(r.json()["duration"] - 1.0) < 0.01
    assert [m["id"] for m in c.get("/v1/music").json()["data"]] == [mid]
    base = decode(speech(c, input="một hai ba bốn năm sáu"))[0]
    mixed = decode(speech(c, input="một hai ba bốn năm sáu", music={"id": mid, "volume_db": -6, "fade_s": 0.5}))[0]
    assert len(mixed) == len(base) and not np.allclose(mixed, base)
    assert speech(c, music={"id": "nope"}).status_code == 400
    assert c.post("/v1/music", files={"file": ("x.wav", b"not audio", "audio/wav")}).status_code == 400
    assert c.delete(f"/v1/music/{mid}").status_code == 200
    assert c.delete(f"/v1/music/{mid}").status_code == 404


# ---- jobs ----
class Receiver:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append((dict(self.headers), body))
                code = outer.statuses[min(len(outer.requests) - 1, len(outer.statuses) - 1)]
                self.send_response(code)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"

    def close(self):
        self.server.shutdown()


def test_job_lifecycle_webhook_signature_and_downloads(env):
    c, eng, _ = env
    rx = Receiver([200])
    try:
        text = "Đoạn một có nội dung.\n\nĐoạn hai tiếp theo.\n\n[voice=Beta] Đoạn ba khác giọng."
        r = c.post("/v1/jobs", json={"input": text, "voice": "Alpha", "webhook_url": rx.url})
        assert r.status_code == 202
        jid = r.json()["id"]
        job = wait_job(c, jid)
        assert job["status"] == "done" and job["progress"] == 1.0
        assert job["segments_total"] == 3 and job["segments_done"] == 3
        assert job["audio_url"] == f"/v1/jobs/{jid}/audio" and job["history_id"]
        audio = c.get(job["audio_url"])
        assert audio.status_code == 200 and len(decode(audio)[0]) > SR
        srt = c.get(job["srt_url"]).text
        vtt = c.get(job["vtt_url"]).text
        assert "Đoạn hai" in srt and vtt.startswith("WEBVTT")
        assert [j["id"] for j in c.get("/v1/jobs").json()["data"]] == [jid]
        end = time.time() + 5
        while not rx.requests and time.time() < end:
            time.sleep(0.05)
        headers, body = rx.requests[0]
        secret = config.load().webhook_secret
        expect = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert {k.lower(): v for k, v in headers.items()}["x-tqd-signature"] == expect
        payload = json.loads(body)
        assert payload["event"] == "job.done" and payload["job"]["id"] == jid
        assert wait_job(c, jid)["webhook_attempts"][0]["status"] == 200
    finally:
        rx.close()


def test_job_audio_matches_sync_for_single_segment(env):
    c, _, _ = env
    sync = decode(speech(c, input="Một câu đơn giản thôi"))[0]
    jid = c.post("/v1/jobs", json={"input": "Một câu đơn giản thôi", "voice": "Alpha", "response_format": "wav",
                                   "sample_rate": 24000}).json()["id"]
    wait_job(c, jid)
    job_audio = decode(c.get(f"/v1/jobs/{jid}/audio"))[0]
    assert len(job_audio) == len(sync) and np.allclose(job_audio, sync, atol=1e-3)


def test_webhook_three_attempts_on_500(env):
    c, _, _ = env
    rx = Receiver([500, 500, 500])
    try:
        jid = c.post("/v1/jobs", json={"input": "Một hai ba", "voice": "Alpha", "webhook_url": rx.url}).json()["id"]
        wait_job(c, jid)
        end = time.time() + 5
        while len(rx.requests) < 3 and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.2)
        assert len(rx.requests) == 3
        attempts = wait_job(c, jid)["webhook_attempts"]
        assert [a["status"] for a in attempts] == [500, 500, 500]
    finally:
        rx.close()


def test_job_cancel_running_and_queued(env):
    c, eng, _ = env
    eng.gate = threading.Event()
    started = threading.Event()
    eng.on_call = lambda t: started.set()
    j1 = c.post("/v1/jobs", json={"input": "Đoạn một.\n\nĐoạn hai.\n\nĐoạn ba.", "voice": "Alpha"}).json()["id"]
    j2 = c.post("/v1/jobs", json={"input": "Việc thứ hai", "voice": "Alpha"}).json()["id"]
    assert started.wait(5)
    assert c.post(f"/v1/jobs/{j2}/cancel").json()["status"] == "canceled"
    assert c.post(f"/v1/jobs/{j1}/cancel").status_code == 200
    eng.gate.set()
    assert wait_job(c, j1)["status"] == "canceled"
    assert c.get(f"/v1/jobs/{j2}").json()["status"] == "canceled"
    assert c.get(f"/v1/jobs/{j1}/audio").status_code == 409
    assert c.get("/v1/jobs/nope").status_code == 404


def test_job_validation_errors(env):
    c, _, _ = env
    assert c.post("/v1/jobs", json={"input": "x y", "voice": "Zzz"}).status_code == 400
    assert c.post("/v1/jobs", json={"input": "x [pause:99] y", "voice": "Alpha"}).status_code == 400
    assert c.post("/v1/jobs", json={"input": "x y", "voice": "Alpha", "webhook_url": "ftp://a"}).status_code == 400
    assert c.post("/v1/jobs", json={"input": "x y", "voice": "Alpha", "music": {"id": "nope"}}).status_code == 400


def test_job_failure_is_recorded(env):
    c, eng, _ = env

    def boom(text):
        raise RuntimeError("hỏng")

    eng.on_call = boom
    jid = c.post("/v1/jobs", json={"input": "Một hai", "voice": "Alpha"}).json()["id"]
    job = wait_job(c, jid)
    assert job["status"] == "failed" and "hỏng" in job["error"]


def test_job_ratings_by_segment(env):
    c, _, _ = env
    jid = c.post("/v1/jobs", json={"input": "Đoạn một.\n\nĐoạn hai.", "voice": "Alpha"}).json()["id"]
    wait_job(c, jid)
    assert c.post("/v1/ratings", json={"job_id": jid, "segment_index": 1, "rating": -1, "note": "lỗi"}).status_code == 201
    row = c.get("/v1/ratings").json()["data"][0]
    assert row["job_id"] == jid and row["text"] == "Đoạn hai." and row["segment_index"] == 1


# ---- consent ----
def test_clone_requires_consent(env):
    c, _, _ = env
    r = c.post("/v1/voices", files={"file": ("a.wav", wav_bytes(_tone(1, 200)), "audio/wav")}, data={"name": "x"})
    assert r.status_code == 400 and "consent" in r.json()["detail"]
    r = c.post("/v1/voices", files={"file": ("a.wav", wav_bytes(_tone(1, 200)), "audio/wav")},
               data={"name": "x", "consent": "false"})
    assert r.status_code == 400


# ---- import ----
def test_import_endpoint_txt_md_and_errors(env):
    c, _, _ = env
    r = c.post("/v1/import", files={"file": ("a.txt", "Tiêu đề\n\nMột hai ba.".encode("utf-8"))})
    assert r.status_code == 200 and r.json()["title"] == "Tiêu đề" and r.json()["words"] == 5
    r = c.post("/v1/import", files={"file": ("a.md", "# Tiêu đề\n\nĐoạn [link](http://x).".encode("utf-8"))})
    assert "http" not in r.json()["text"]
    assert c.post("/v1/import", files={"file": ("a.exe", b"x")}).status_code == 400
    assert c.post("/v1/import", json={}).status_code == 400


def test_import_missing_dependency_is_501(env, monkeypatch):
    from vizipvoice_addons import importers

    def missing(data):
        raise importers.MissingDependency("pypdf")

    monkeypatch.setattr(importers, "from_pdf", missing)
    c, _, _ = env
    r = c.post("/v1/import", files={"file": ("a.pdf", b"%PDF")})
    assert r.status_code == 501 and "uv tool install" in r.json()["detail"]
