import json
import time
from argparse import Namespace
from dataclasses import replace

import huggingface_hub
import numpy as np
import pytest
import soundfile as sf

from tqd_voicestudio import config, install, jobs, webhook
from tqd_voicestudio.doctor import Facts
from tqd_voicestudio.store import Store

SR = 24000


def split_runs(text, default):
    return [(default, text)]


def test_plan_segments_voice_paragraph_sentence_joins():
    def runs(text, default):
        return [("A", "một hai\n\nba bốn"), ("B", "năm sáu")]

    plan = jobs.plan_segments("x", "A", runs)
    assert [(p["voice"], p["join"]) for p in plan] == [("A", None), ("A", "paragraph"), ("B", "voice")]
    long_para = " ".join(["Câu số một."] * 300)
    plan = jobs.plan_segments(long_para, "A", split_runs)
    assert len(plan) > 1 and plan[0]["join"] is None and all(p["join"] == "sentence" for p in plan[1:])
    assert all(len(p["text"].split()) <= 400 for p in plan)


def make_manager(tmp_path, render, assemble=None, **kw):
    store = Store(tmp_path / "studio.db")
    assembled = {}

    def default_assemble(request, wav, segments, pauses, subs):
        assembled["wav"], assembled["segments"] = wav, segments
        return "hist"

    mgr = jobs.JobManager(store, tmp_path / "jobs", render=render, assemble=assemble or default_assemble,
                          webhook_secret=lambda: "s", backoff=(0, 0, 0), start=False, **kw)
    return store, mgr, assembled


def tone(n):
    return np.full(n, 0.1, dtype=np.float32)


def test_resume_skips_existing_segment_files(tmp_path):
    calls = []

    def render(request, seg):
        calls.append(seg["text"])
        return tone(SR), [{"start": 0.0, "end": 1.0, "text": seg["text"], "voice": "A"}], []

    store, mgr, assembled = make_manager(tmp_path, render)
    plan = [{"voice": "A", "text": t, "join": None if i == 0 else "paragraph"} for i, t in enumerate(["a", "b", "c"])]
    jid = store.add_job({"input": "x"}, plan, None, True)
    store.update_job(jid, status="running", segments_done=1)
    d = tmp_path / "jobs" / jid
    d.mkdir(parents=True)
    sf.write(str(d / "seg-0000.wav"), tone(SR), SR, subtype="FLOAT")
    (d / "seg-0000.json").write_text(json.dumps({"segments": [{"start": 0.0, "end": 1.0, "text": "a", "voice": "A"}],
                                                 "pauses": [], "samples": SR}), encoding="utf-8")
    mgr2 = jobs.JobManager(store, tmp_path / "jobs", render=render,
                           assemble=lambda *a: assembled.update(n=len(a[1])) or "hist",
                           webhook_secret=lambda: "s", backoff=(0, 0, 0))
    end = time.time() + 5
    while store.get_job(jid)["status"] != "done" and time.time() < end:
        time.sleep(0.05)
    mgr2.stop()
    assert store.get_job(jid)["status"] == "done"
    assert calls == ["b", "c"]
    expected = SR * 3 + 2 * int(SR * 0.48)
    assert assembled["n"] == expected


def test_prune_jobs_keeps_exact_cap_and_removes_old_dirs(tmp_path):
    store, mgr, _ = make_manager(tmp_path, lambda r, s: (tone(10), [], []), jobs_max=2)
    ids = []
    for i in range(4):
        jid = store.add_job({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
        store.update_job(jid, status="done", finished_at=time.time() + i)
        d = tmp_path / "jobs" / jid
        d.mkdir(parents=True)
        (d / "leftover.txt").write_text("x")
        ids.append(jid)
    assert mgr.prune_jobs() == 2
    assert [r["id"] for r in store.list_jobs()] == ids[-2:][::-1]
    assert not (tmp_path / "jobs" / ids[0]).exists() and not (tmp_path / "jobs" / ids[1]).exists()
    assert (tmp_path / "jobs" / ids[2]).exists() and (tmp_path / "jobs" / ids[3]).exists()
    store.close()


def test_store_update_failure_in_failure_handler_marks_job_failed(tmp_path, caplog, monkeypatch):
    def render(request, seg):
        raise RuntimeError("render exploded")

    store, mgr, _ = make_manager(tmp_path, render)
    orig_update = store.update_job
    raised = {"done": False}

    def flaky_update(jid, **fields):
        if fields.get("status") == "failed" and not raised["done"]:
            raised["done"] = True
            raise RuntimeError("sqlite write failed")
        return orig_update(jid, **fields)

    monkeypatch.setattr(store, "update_job", flaky_update)
    jid = mgr.submit({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
    mgr.start()
    end = time.time() + 5
    while time.time() < end:
        row = store.get_job(jid)
        if row["status"] == "failed":
            break
        time.sleep(0.05)
    mgr.stop()
    row = store.get_job(jid)
    assert row["status"] == "failed" and row["finished_at"] is not None
    assert raised["done"] and "job worker failed" in caplog.text
    store.close()


def test_canceled_queued_job_never_renders_when_worker_claims(tmp_path):
    calls = []

    def render(request, seg):
        calls.append(seg["text"])
        return tone(10), [], []

    store, mgr, _ = make_manager(tmp_path, render)
    jid = mgr.submit({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
    assert mgr.cancel(jid) == "canceled"
    mgr.start()
    time.sleep(0.3)
    mgr.stop()
    assert calls == []
    row = store.get_job(jid)
    assert row["status"] == "canceled"
    assert not store.claim_job(jid), "a canceled row must fail the atomic queued-only claim"
    store.close()


def test_restart_requeues_running_jobs(tmp_path):
    store, mgr, _ = make_manager(tmp_path, lambda r, s: (tone(10), [], []))
    jid = store.add_job({"input": "x"}, [{"voice": "A", "text": "a", "join": None}], None, True)
    store.update_job(jid, status="running")
    jobs.JobManager(store, tmp_path / "jobs", render=lambda r, s: (tone(10), [], []),
                    assemble=lambda *a: "h", webhook_secret=lambda: "s", start=False)
    assert store.get_job(jid)["status"] == "queued"


def test_webhook_sign_and_retry_logic():
    body = b'{"a":1}'
    assert webhook.sign("k", body).startswith("sha256=") and len(webhook.sign("k", body)) == 7 + 64
    sent, slept = [], []

    def post(url, data, headers, timeout):
        sent.append((url, data, headers))
        return 500 if len(sent) < 3 else 200

    rec = webhook.deliver("http://x", {"event": "job.done"}, "k", backoff=(1, 4, 16), sleep=slept.append, post=post)
    assert [r["status"] for r in rec] == [500, 500, 200] and slept == [1, 4]
    assert sent[0][2]["X-TQD-Signature"] == webhook.sign("k", sent[0][1])

    def always_down(*a):
        raise ConnectionError("down")

    rec = webhook.deliver("http://x", {}, "k", backoff=(0, 0, 0), sleep=lambda s: None, post=always_down)
    assert len(rec) == 3 and all(r["error"] for r in rec)


# ---- config ----
def test_webhook_secret_generated_once_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    s1 = config.ensure_webhook_secret()
    assert len(s1) >= 32 and config.ensure_webhook_secret() == s1 and config.load().webhook_secret == s1
    assert config.load().history_max == 500 and config.load().jobs_max == 100


def _facts(**kw):
    base = Facts(
        os="Windows", arch="AMD64", python="3.12.0", torch_version="2.8.0", torch_cuda="12.8",
        cuda_available=True, gpu_name="RTX 3060", vram_total_gb=12.0, nvidia_smi=True,
        driver_version="570.0", ram_total_gb=32.0, ram_avail_gb=20.0, disk_free_gb=100.0,
        ffmpeg="ffmpeg", port=8765, port_state="free", model_present=True,
    )
    return replace(base, **kw)


def test_install_merges_user_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    for name in ("HF_HOME", "VIZIPVOICE_MODEL_DIR", "VIZIPVOICE_VOICES_DIR"):
        monkeypatch.delenv(name, raising=False)
    config.save(config.Config(default_voice="Nhã-Uyên", library_dir="X:/lib", webhook_secret="sec",
                              history_max=7, device="cpu", fp16=False, max_streams=1, num_step=24, port=9000))
    monkeypatch.setattr(install.doctor, "collect", lambda cfg: _facts())
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: "")
    assert install.run(Namespace(no_asr=True, no_smoke=True, voices=None, yes=False)) == 0
    cfg = config.load()
    assert (cfg.default_voice, cfg.library_dir, cfg.webhook_secret, cfg.history_max) == ("Nhã-Uyên", "X:/lib", "sec", 7)
    assert (cfg.num_step, cfg.port) == (24, 9000)
    assert (cfg.device, cfg.fp16, cfg.max_streams) == ("cuda", True, 4)


def test_merge_recommended_only_touches_doctor_fields():
    cur = config.Config(default_voice="X", library_dir="L", webhook_secret="S", history_max=3, port=1)
    rec = config.Config(device="cuda", fp16=True, max_streams=4, default_voice="DEFAULT")
    out = config.merge_recommended(cur, rec)
    assert (out.default_voice, out.library_dir, out.webhook_secret, out.history_max, out.port) == ("X", "L", "S", 3, 1)
    assert (out.device, out.max_streams) == ("cuda", 4)
