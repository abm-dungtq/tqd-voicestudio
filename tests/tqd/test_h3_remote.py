import asyncio
import base64
import io
import json
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

import test_h2_server as h2
import vizipvoice_openai_server as srv
from tqd_voicestudio import cli, config, daemon, mcp_config, netsec

KEY = "k3y-for-tests"


# ---- loopback rule ----
@pytest.mark.parametrize("host,loop", [
    ("127.0.0.1", True), ("127.1.2.3", True), ("::1", True), ("[::1]", True), ("localhost", True), ("LOCALHOST", True),
    ("0.0.0.0", False), ("::", False), ("", False), ("100.80.65.15", False), ("192.168.1.5", False),
    ("example.com", False), ("10.0.0.1", False),
])
def test_loopback_matrix(host, loop):
    assert netsec.is_loopback(host) is loop
    assert netsec.requires_key(host, "") is (not loop)
    assert netsec.requires_key(host, "key") is False


def test_connect_host():
    assert netsec.connect_host("0.0.0.0") == "127.0.0.1"
    assert netsec.connect_host("::") == "127.0.0.1"
    assert netsec.connect_host("localhost") == "127.0.0.1"
    assert netsec.connect_host("100.80.65.15") == "100.80.65.15"


# ---- refusals ----
def test_serve_refuses_without_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2
    assert "key new" in capsys.readouterr().err
    config.save(config.Config(host="100.80.65.15"))
    assert cli.main(["serve"]) == 2


def test_daemon_spawn_refuses_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    monkeypatch.setattr(daemon, "health", lambda cfg, timeout=1.0: None)

    def boom(*a, **k):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(daemon, "_spawn_wmi", boom)
    monkeypatch.setattr(daemon, "_spawn_posix", boom)
    monkeypatch.setattr(daemon, "_spawn_detached", boom)
    with pytest.raises(RuntimeError, match="API key"):
        daemon.ensure_server(config.Config(host="0.0.0.0"))


def test_server_main_refuses_without_key(monkeypatch):
    monkeypatch.setenv("PORT_HOST", "0.0.0.0")
    monkeypatch.setattr(srv, "API_KEY", "")
    with pytest.raises(SystemExit) as exc:
        srv.main()
    assert exc.value.code == 2


def test_config_env_host_key_and_client_urls(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    env = config.to_env(config.Config(host="0.0.0.0", api_key="abc"))
    assert env["PORT_HOST"] == "0.0.0.0" and env["VIZIPVOICE_API_KEY"] == "abc"
    assert "VIZIPVOICE_API_KEY" not in config.to_env(config.Config())
    assert daemon.base_url(config.Config(host="0.0.0.0", port=9)) == "http://127.0.0.1:9"
    assert daemon.auth_headers(config.Config(api_key="abc")) == {"Authorization": "Bearer abc"}
    assert daemon.auth_headers(config.Config()) == {}


def test_install_merge_keeps_host_and_api_key():
    cur = config.Config(host="0.0.0.0", api_key="secret", port=1)
    out = config.merge_recommended(cur, config.Config(device="cuda", fp16=True, max_streams=4))
    assert (out.host, out.api_key, out.port) == ("0.0.0.0", "secret", 1)


def test_key_new_show(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    assert cli.main(["key", "show"]) == 1
    capsys.readouterr()
    assert cli.main(["key", "new"]) == 0
    out = capsys.readouterr()
    key = out.out.strip()
    assert len(key) >= 32 and "khởi động lại" in out.err.lower() or "Khởi động lại" in out.err
    assert config.load().api_key == key
    assert cli.main(["key", "show"]) == 0 and capsys.readouterr().out.strip() == key


# ---- mcp-config --remote ----
def test_mcp_config_remote_placeholder_and_show_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    monkeypatch.setattr(mcp_config, "resolve_exe", lambda: "C:/x/tqd-voicestudio.exe")
    assert cli.main(["mcp-config", "--remote", "http://100.80.65.15:8765"]) == 0
    out = capsys.readouterr().out
    assert 'claude mcp add --transport http tqd-voicestudio http://100.80.65.15:8765/mcp --header "Authorization: Bearer <KEY>"' in out
    assert '"url": "http://100.80.65.15:8765/mcp"' in out and '"Authorization": "Bearer <KEY>"' in out
    assert '"VIZIPVOICE_API_URL": "http://100.80.65.15:8765"' in out and '"VIZIPVOICE_API_KEY": "<KEY>"' in out
    assert '"command": "C:/x/tqd-voicestudio.exe"' in out
    key = config.generate_api_key()
    assert cli.main(["mcp-config", "--remote", "http://h:1/mcp", "--show-key"]) == 0
    shown = capsys.readouterr().out
    assert shown.count(key) == 3 and "<KEY>" not in shown and "http://h:1/mcp" in shown and "/mcp/mcp" not in shown
    assert cli.main(["mcp-config", "--remote", "http://h:1", "--key-env", "TQD_KEY"]) == 0
    env_out = capsys.readouterr().out
    assert "${TQD_KEY}" in env_out and "${env:TQD_KEY}" in env_out and key not in env_out


def test_mcp_config_show_key_without_key_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    assert cli.main(["mcp-config", "--remote", "http://h:1", "--show-key"]) == 1


# ---- auth on every route ----
@pytest.fixture
def secured(monkeypatch, tmp_path):
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    monkeypatch.setenv("VIZIPVOICE_LEXICON_PATH", str(tmp_path / "lex.json"))
    monkeypatch.setattr(srv, "API_KEY", KEY)
    monkeypatch.setattr(srv, "engine", h2.FakeEngine())
    monkeypatch.setattr(srv, "_STORE", None)
    monkeypatch.setattr(srv, "_JOBS", None)
    monkeypatch.setattr(srv.mcp_gateway, "handler", None)
    yield TestClient(srv.app, raise_server_exceptions=False)
    if srv._STORE is not None:
        srv._STORE.close()


def _concrete(path: str) -> str:
    return path.replace("{jid}", "x").replace("{hid}", "x").replace("{mid}", "x")


def _all_route_methods():
    for route in srv.app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in sorted(getattr(route, "methods", None) or {"GET"}):
            if method in ("HEAD", "OPTIONS"):
                continue
            yield method, _concrete(path)


def test_every_route_requires_key_except_allow_list(secured):
    routes = list(_all_route_methods())
    assert ("POST", "/mcp") in routes and ("GET", "/health") in routes and len(routes) > 25
    for method, path in routes:
        r = secured.request(method, path)
        if srv.is_open_route(method, path):
            assert r.status_code != 401, (method, path)
        else:
            assert r.status_code == 401, (method, path, r.status_code)
            assert r.headers["www-authenticate"] == "Bearer"
        bad = secured.request(method, path, headers={"Authorization": "Bearer wrong"})
        assert bad.status_code == (200 if path == "/health" else 401) or srv.is_open_route(method, path), (method, path)


def test_routes_accept_correct_key_and_health_is_open(secured):
    h = {"Authorization": f"Bearer {KEY}"}
    assert secured.get("/health").status_code == 200
    assert secured.get("/v1/voices").status_code == 401
    assert secured.get("/v1/voices", headers=h).status_code == 200
    assert secured.get("/v1/history", headers=h).status_code == 200
    assert secured.post("/mcp", headers=h).status_code == 503
    for method, path in _all_route_methods():
        assert secured.request(method, path, headers=h).status_code != 401, (method, path)
    assert srv.is_open_route("GET", "/") and srv.is_open_route("GET", "/static/app.js")
    assert not srv.is_open_route("POST", "/health") and not srv.is_open_route("GET", "/mcp")
    assert not srv.is_open_route("GET", "/v1/voices") and not srv.is_open_route("GET", "/docs")


def test_no_key_means_open(monkeypatch, secured):
    monkeypatch.setattr(srv, "API_KEY", "")
    assert secured.get("/v1/voices").status_code == 200


# ---- MCP over HTTP ----
class McpEngine(h2.FakeEngine):
    def __init__(self):
        super().__init__()
        self.enrolled = []

    def enroll_voice(self, name, wav_bytes, prompt_text, description, force, gender, region, auto_transcribe):
        self.enrolled.append((name, len(wav_bytes), gender, region))
        self.voices[name] = SimpleNamespace(name=name, kind="custom")
        return SimpleNamespace(name=name, description=description or "Enrolled", prompt_text="lời", wav_path="",
                               transcript_source="asr", gender=gender, region=region)


@pytest.fixture
def live(monkeypatch, tmp_path):
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setenv("TQD_VOICESTUDIO_HOME", str(tmp_path))
    monkeypatch.setenv("PORT", str(port))
    monkeypatch.setattr(srv, "API_KEY", KEY)
    monkeypatch.setattr(srv, "ViZipVoiceEngine", McpEngine)
    monkeypatch.setattr(srv, "_STORE", None)
    monkeypatch.setattr(srv, "_JOBS", None)
    import mcp_server

    for name, val in (("HTTP_MODE", mcp_server.HTTP_MODE), ("API_URL", mcp_server.API_URL), ("API_KEY", mcp_server.API_KEY),
                      ("INLINE_AUDIO_LIMIT", mcp_server.INLINE_AUDIO_LIMIT)):
        monkeypatch.setattr(mcp_server, name, val)
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    end = time.time() + 15
    while not server.started and time.time() < end:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(10)
    if srv._JOBS is not None:
        srv._JOBS.stop()


async def _mcp(url, fn, path="/mcp"):
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}, timeout=60) as http:
        async with Client(streamable_http_client(url + path, http_client=http)) as client:
            return await fn(client)


def _blocks(result):
    audio = [b for b in result.content if b.type == "audio"]
    text = [json.loads(b.text) for b in result.content if b.type == "text"]
    return audio, text


def test_mcp_http_requires_key(live):
    r = httpx.post(live + "/mcp", json={})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_mcp_trailing_slash_behaves_the_same_and_client_accepts_it(live):
    h = {"Authorization": f"Bearer {KEY}"}
    plain = httpx.post(live + "/mcp", json={}, headers=h)
    slash = httpx.post(live + "/mcp/", json={}, headers=h)
    assert slash.status_code == plain.status_code

    async def run(client):
        return {t.name for t in (await client.list_tools()).tools}

    tools = asyncio.run(_mcp(live, run, path="/mcp/"))
    assert "list_voices" in tools


def test_auth_middleware_rejects_websocket_scope_without_calling_app():
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(srv.BearerAuthMiddleware(app)({"type": "websocket"}, None, send))
    assert not called and sent == [{"type": "websocket.close", "code": 1008}]


def test_mcp_http_tools_audio_inline_then_path_only(live, monkeypatch):
    import mcp_server

    async def run(client):
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"list_voices", "synthesize_speech", "create_voice", "create_job", "job_status"} <= tools
        voices = await client.call_tool("list_voices", {})
        assert "Alpha" in voices.content[0].text
        small = await client.call_tool("synthesize_speech", {"text": "Xin chào các bạn", "voice_id": "Alpha",
                                                              "sample_rate": 24000})
        monkeypatch.setattr(mcp_server, "INLINE_AUDIO_LIMIT", 10)
        big = await client.call_tool("synthesize_speech", {"text": "Một câu khác để thử", "voice_id": "Alpha",
                                                            "sample_rate": 24000})
        return small, big

    small, big = asyncio.run(_mcp(live, run))
    audio, text = _blocks(small)
    assert len(audio) == 1 and audio[0].mime_type == "audio/wav"
    wav, sr = sf.read(io.BytesIO(base64.b64decode(audio[0].data)), dtype="float32")
    assert sr == 24000 and len(wav) > 10000 // 2
    assert "path" not in text[0] and text[0]["history_id"]
    assert text[0]["download_path"] == f"/v1/history/{text[0]['history_id']}/audio"
    fetched = httpx.get(live + text[0]["download_path"], headers={"Authorization": f"Bearer {KEY}"})
    assert fetched.status_code == 200 and len(fetched.content) == text[0]["size_bytes"]
    assert httpx.get(live + text[0]["download_path"]).status_code == 401
    audio2, text2 = _blocks(big)
    assert audio2 == [] and text2[0]["download_path"].startswith("/v1/history/") and "note" in text2[0]


def test_mcp_http_create_voice_base64_and_consent(live):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(4800, np.float32), 24000, format="WAV")
    b64 = base64.b64encode(buf.getvalue()).decode()

    async def run(client):
        ok = await client.call_tool("create_voice", {"name": "remote1", "audio_base64": b64, "filename": "r.wav",
                                                     "consent": True, "gender": "female"})
        no_consent = await client.call_tool("create_voice", {"name": "remote2", "audio_base64": b64})
        path = await client.call_tool("create_voice", {"name": "remote3", "reference_audio_path": "C:/x.wav",
                                                       "consent": True})
        return ok, no_consent, path

    ok, no_consent, path = asyncio.run(_mcp(live, run))
    assert not ok.is_error and json.loads(ok.content[0].text)["voice"]["name"] == "remote1"
    assert srv.engine.enrolled == [("remote1", len(buf.getvalue()), "female", "")]
    assert no_consent.is_error and path.is_error
    assert srv.engine.enrolled == [("remote1", len(buf.getvalue()), "female", "")]


def test_mcp_http_jobs(live):
    async def run(client):
        created = await client.call_tool("create_job", {"text": "Đoạn một.\n\nĐoạn hai.", "voice_id": "Alpha"})
        jid = json.loads(created.content[0].text)["id"]
        for _ in range(100):
            st = json.loads((await client.call_tool("job_status", {"job_id": jid})).content[0].text)
            if st["status"] in ("done", "failed"):
                return st
            await asyncio.sleep(0.1)

    st = asyncio.run(_mcp(live, run))
    assert st["status"] == "done" and st["audio_url"].endswith("/audio") and st["srt_url"].endswith(".srt")
