#!/usr/bin/env python3
"""Remote MCP smoke: Streamable HTTP /mcp with Bearer auth.

  python scripts/smoke/remote_mcp_smoke.py --url http://100.80.65.15:8765/mcp --key K

Asserts: 401 without key, list_voices, synthesize_speech returns inline AudioContent that decodes
as a WAV > 10 KB, and the history audio can be fetched over HTTP with the key.
Prints REMOTE_MCP_OK on success.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
from urllib.parse import urlsplit

import httpx
import soundfile as sf

TEXT = "Xin chào, đây là thử nghiệm MCP từ xa của TQD VoiceStudio."


async def run(url: str, key: str) -> str:
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"

    r = httpx.post(url, json={}, timeout=30)
    assert r.status_code == 401, f"expected 401 without key, got {r.status_code}"
    assert httpx.get(origin + "/v1/voices", timeout=30).status_code == 401, "REST must also require the key"
    assert httpx.get(origin + "/health", timeout=30).status_code == 200, "/health must stay open"

    headers = {"Authorization": f"Bearer {key}"}
    async with httpx2.AsyncClient(headers=headers, timeout=300) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"list_voices", "synthesize_speech", "create_voice", "create_job", "job_status"} <= tools, tools
            voices = await client.call_tool("list_voices", {})
            assert not voices.is_error and "Đinh-Quyết" in voices.content[0].text, "list_voices missing Đinh-Quyết"
            res = await client.call_tool("synthesize_speech", {"text": TEXT, "sample_rate": 24000})
    assert not res.is_error, res
    audio = [b for b in res.content if b.type == "audio"]
    text = [json.loads(b.text) for b in res.content if b.type == "text"]
    assert len(audio) == 1, "expected one inline AudioContent"
    raw = base64.b64decode(audio[0].data)
    assert len(raw) > 10_000, f"audio too small: {len(raw)}"
    wav, sr = sf.read(io.BytesIO(raw), dtype="float32")
    assert audio[0].mime_type == "audio/wav" and sr == 24000 and len(wav) > 0
    hid = text[0]["history_id"]
    fetched = httpx.get(origin + text[0]["download_path"], headers=headers, timeout=60)
    assert fetched.status_code == 200 and len(fetched.content) == len(raw), "history audio fetch failed"
    assert httpx.get(origin + text[0]["download_path"], timeout=30).status_code == 401
    return hid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="MCP endpoint, e.g. http://100.80.65.15:8765/mcp")
    ap.add_argument("--key", required=True)
    args = ap.parse_args()
    try:
        hid = asyncio.run(asyncio.wait_for(run(args.url, args.key), 400))
    except Exception as exc:
        print(f"REMOTE_MCP_FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"REMOTE_MCP_OK history_id={hid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
