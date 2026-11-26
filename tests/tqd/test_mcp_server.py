import asyncio

import httpx

import mcp_server


def _run_create_voice(monkeypatch, tmp_path, **kwargs):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"RIFF....WAVE")
    calls = []

    async def fake_request(method, path, **kw):
        calls.append((method, path, kw))
        return httpx.Response(201, json={"name": "x"})

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    asyncio.run(mcp_server.create_voice("x", str(wav), **kwargs))
    assert len(calls) == 1
    method, path, kw = calls[0]
    assert (method, path) == ("POST", "/v1/voices")
    return kw["data"]


def test_create_voice_omits_transcript_when_absent(monkeypatch, tmp_path):
    form = _run_create_voice(monkeypatch, tmp_path)
    assert "ref_text" not in form and "prompt_text" not in form


def test_create_voice_sends_transcript(monkeypatch, tmp_path):
    form = _run_create_voice(monkeypatch, tmp_path, ref_text="abc")
    assert form["ref_text"] == "abc"


def test_create_voice_sends_gender_and_region(monkeypatch, tmp_path):
    form = _run_create_voice(monkeypatch, tmp_path, gender="female", region="nam")
    assert form["gender"] == "female" and form["region"] == "nam"
