#!/usr/bin/env python3
"""ViZipVoice MCP server (stdio) — Vietnamese TTS tools for MCP clients.

Bridges MCP clients (Claude Desktop, IDE agents) to the ViZipVoice
OpenAI-compatible speech server (vizipvoice_openai_server.py).

Environment:
  VIZIPVOICE_API_URL      Speech server base URL (default: start/reuse the local tqd-voicestudio
                          daemon on 127.0.0.1:8765 on the first tool call)
  VIZIPVOICE_API_KEY      Bearer token; leave empty when the server runs without auth
  VIZIPVOICE_OUTPUT_DIR   Directory for synthesized audio (default: <TQD_VOICESTUDIO_HOME>/outputs/mcp)

Run:  python mcp_server.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
from mcp.server.mcpserver import MCPServer

import tqd_voicestudio
from tqd_voicestudio import config, daemon, paths

log = logging.getLogger("vizipvoice.mcp")

API_URL: Optional[str] = (os.environ.get("VIZIPVOICE_API_URL") or "").rstrip("/") or None
_api_lock: Optional[asyncio.Lock] = None
API_KEY = os.environ.get("VIZIPVOICE_API_KEY", "").strip()
OUTPUT_DIR = Path(os.environ.get("VIZIPVOICE_OUTPUT_DIR") or paths.outputs_dir() / "mcp")

SUPPORTED_RATES = (8000, 16000, 24000, 48000)
SUPPORTED_FORMATS = ("wav", "pcm", "mp3", "flac", "opus")
MAX_REFERENCE_BYTES = 25 * 1024 * 1024

mcp = MCPServer(
    name="tqd-voicestudio",
    version=tqd_voicestudio.__version__,
    instructions=(
        "Vietnamese text-to-speech via ViZipVoice. Use synthesize_speech to render "
        "Vietnamese text to an audio file, list_voices to discover voice IDs, and "
        "create_voice to enroll a new voice from a reference clip. "
        "Lần gọi đầu tiên có thể mất 1–2 phút để nạp model."
    ),
)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


async def _api_url() -> str:
    global API_URL, _api_lock
    if API_URL:
        return API_URL
    if _api_lock is None:
        _api_lock = asyncio.Lock()
    async with _api_lock:
        if not API_URL:
            API_URL = await asyncio.to_thread(daemon.ensure_server, config.load())
    return API_URL


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Call the speech server and surface HTTP errors with response detail."""
    base_url = await _api_url()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=10.0)
        ) as client:
            resp = await client.request(
                method, f"{base_url}{path}", headers=_headers(), **kwargs
            )
            resp.raise_for_status()
            return resp
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise RuntimeError(
            f"speech API {exc.response.status_code} on {path}: {detail}"
        ) from exc
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"cannot reach ViZipVoice server at {base_url}; start "
            "vizipvoice_openai_server.py or set VIZIPVOICE_API_URL"
        ) from exc


def _audio_duration_seconds(content: bytes, fmt: str, sample_rate: int) -> Optional[float]:
    if fmt not in ("wav", "pcm"):
        return None
    if fmt == "wav":
        with wave.open(BytesIO(content), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or sample_rate
            return round(frames / float(rate), 3) if rate else 0.0
    return round(len(content) / float(sample_rate * 2), 3)


@mcp.tool()
async def list_voices() -> str:
    """List every voice the server can synthesize with, including aliases and enrolled voices."""
    resp = await _request("GET", "/v1/voices")
    return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool()
async def synthesize_speech(
    text: str,
    voice_id: Optional[str] = None,
    speed: Optional[float] = None,
    style: Optional[str] = "auto",
    sample_rate: int = 48000,
    response_format: str = "wav",
    return_base64: bool = False,
) -> str:
    """Render Vietnamese text to speech and save it as an audio file.

    Returns JSON with the output path, byte size, duration, and optional base64.
    """
    if response_format not in SUPPORTED_FORMATS:
        raise ValueError(f"response_format must be one of {SUPPORTED_FORMATS}")
    if sample_rate not in SUPPORTED_RATES:
        raise ValueError(f"sample_rate must be one of {SUPPORTED_RATES}")
    if not text.strip():
        raise ValueError("text must not be empty")
    if speed is not None and not 0.5 <= speed <= 2.0:
        raise ValueError("speed must be within 0.5-2.0")

    voice_id = voice_id or config.load().default_voice
    payload: dict[str, Any] = {
        "model": "vizipvoice-v1",
        "input": text,
        "voice": voice_id,
        "response_format": response_format,
        "sample_rate": sample_rate,
        "style": style,
    }
    if speed is not None:
        payload["speed"] = speed

    log.info("synthesize_speech voice=%s format=%s rate=%d", voice_id, response_format, sample_rate)
    resp = await _request("POST", "/v1/audio/speech", json=payload)
    content = resp.content

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f".{response_format}"
    out_path = OUTPUT_DIR / f"vizipvoice-{uuid.uuid4().hex[:12]}{suffix}"
    out_path.write_bytes(content)

    result: dict[str, Any] = {
        "status": "success",
        "voice": voice_id,
        "format": response_format,
        "sample_rate": sample_rate,
        "path": str(out_path),
        "size_bytes": len(content),
        "duration_seconds": _audio_duration_seconds(content, response_format, sample_rate),
    }
    if return_base64:
        result["audio_base64"] = base64.b64encode(content).decode("ascii")
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def create_voice(
    name: str,
    reference_audio_path: str,
    ref_text: Optional[str] = None,
    gender: Optional[str] = None,
    region: Optional[str] = None,
    description: str = "",
    denoise: bool = True,
    force: bool = False,
) -> str:
    """Enroll a new voice from a local reference clip (3-10s, wav/mp3/flac).

    `ref_text` is the exact transcript of the clip; when omitted the server
    transcribes it. `gender` is male|female, `region` is bac|trung|nam.
    """
    ref_path = Path(reference_audio_path).expanduser()
    if not ref_path.is_file():
        raise ValueError(f"reference audio not found: {ref_path}")
    if ref_path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("reference audio exceeds 25 MB limit")
    if ref_text is not None and not ref_text.strip():
        ref_text = None

    form = {
        "name": name,
        "description": description,
        "denoise": "true" if denoise else "false",
        "force": "true" if force else "false",
    }
    if ref_text is not None:
        form["ref_text"] = ref_text
    if gender:
        form["gender"] = gender
    if region:
        form["region"] = region

    log.info("create_voice name=%s ref=%s", name, ref_path.name)
    resp = await _request(
        "POST",
        "/v1/voices",
        files={"file": (ref_path.name, ref_path.read_bytes(), "application/octet-stream")},
        data=form,
    )
    return json.dumps({"status": "success", "voice": resp.json()}, ensure_ascii=False)


@mcp.resource("vizipvoice://voices", name="voices", mime_type="application/json")
async def voices_resource() -> str:
    """Current voice catalog as JSON."""
    resp = await _request("GET", "/v1/voices")
    return json.dumps(resp.json(), ensure_ascii=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("TQD-VoiceStudio MCP server -> %s", API_URL or "local daemon (on first call)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
