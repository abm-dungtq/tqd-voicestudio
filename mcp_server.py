#!/usr/bin/env python3
"""ViZipVoice MCP server (stdio) — Vietnamese TTS tools for MCP clients.

Bridges MCP clients (Claude Desktop, IDE agents) to the ViZipVoice
OpenAI-compatible speech server (vizipvoice_openai_server.py).

Environment:
  VIZIPVOICE_API_URL      Speech server base URL (default: http://127.0.0.1:8001)
  VIZIPVOICE_API_KEY      Bearer token; leave empty when the server runs without auth
  VIZIPVOICE_OUTPUT_DIR   Directory for synthesized audio (default: <tmp>/vizipvoice-mcp)

Run:  python mcp_server.py
"""

from __future__ import annotations

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

log = logging.getLogger("vizipvoice.mcp")

API_URL = os.environ.get("VIZIPVOICE_API_URL", "http://127.0.0.1:8001").rstrip("/")
API_KEY = os.environ.get("VIZIPVOICE_API_KEY", "").strip()
OUTPUT_DIR = Path(
    os.environ.get("VIZIPVOICE_OUTPUT_DIR")
    or Path(os.environ.get("TEMP", "/tmp")) / "vizipvoice-mcp"
)

SUPPORTED_RATES = (8000, 16000, 24000, 48000)
SUPPORTED_FORMATS = ("wav", "pcm")
MAX_REFERENCE_BYTES = 25 * 1024 * 1024

mcp = MCPServer(
    name="vizipvoice",
    version="1.0.0",
    instructions=(
        "Vietnamese text-to-speech via ViZipVoice. Use synthesize_speech to render "
        "Vietnamese text to an audio file, list_voices to discover voice IDs, and "
        "create_voice to enroll a new voice from a reference clip."
    ),
)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Call the speech server and surface HTTP errors with response detail."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(600.0, connect=10.0)
        ) as client:
            resp = await client.request(
                method, f"{API_URL}{path}", headers=_headers(), **kwargs
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
            f"cannot reach ViZipVoice server at {API_URL}; start "
            "vizipvoice_openai_server.py or set VIZIPVOICE_API_URL"
        ) from exc


def _audio_duration_seconds(content: bytes, fmt: str, sample_rate: int) -> float:
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
    voice_id: str = "Đinh-Quyết",
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
    suffix = ".wav" if response_format == "wav" else ".pcm"
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
    ref_text: str,
    description: str = "",
    denoise: bool = True,
    force: bool = False,
) -> str:
    """Enroll a new voice from a local reference clip (3-10s, wav/mp3/flac).

    `ref_text` must be the exact transcript of the reference clip.
    """
    ref_path = Path(reference_audio_path).expanduser()
    if not ref_path.is_file():
        raise ValueError(f"reference audio not found: {ref_path}")
    if ref_path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("reference audio exceeds 25 MB limit")
    if not ref_text.strip():
        raise ValueError("ref_text must be the exact transcript of the reference audio")

    log.info("create_voice name=%s ref=%s", name, ref_path.name)
    resp = await _request(
        "POST",
        "/v1/voices",
        files={"file": (ref_path.name, ref_path.read_bytes(), "application/octet-stream")},
        data={
            "name": name,
            "description": description,
            "ref_text": ref_text,
            "denoise": "true" if denoise else "false",
            "force": "true" if force else "false",
        },
    )
    return json.dumps({"status": "success", "voice": resp.json()}, ensure_ascii=False)


@mcp.resource("vizipvoice://voices", name="voices", mime_type="application/json")
async def voices_resource() -> str:
    """Current voice catalog as JSON."""
    resp = await _request("GET", "/v1/voices")
    return json.dumps(resp.json(), ensure_ascii=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("ViZipVoice MCP server -> %s", API_URL)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
