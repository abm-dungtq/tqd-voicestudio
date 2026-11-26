#!/usr/bin/env python3
"""TQD-VoiceStudio MCP server — Vietnamese TTS tools for MCP clients.

One set of tool definitions served over two transports:
  * stdio  (``tqd-voicestudio mcp``): bridges a local client to the speech server;
    audio is written to a file on this machine.
  * Streamable HTTP mounted at ``/mcp`` inside the speech server itself (HTTP mode):
    audio is returned inline as MCP AudioContent (<= INLINE_AUDIO_LIMIT) because the
    client is on another machine.

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
from mcp_types import AudioContent, ContentBlock, TextContent

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
INLINE_AUDIO_LIMIT = 8 * 1024 * 1024
HTTP_MODE = False  # True when the tools run inside the speech server (mounted at /mcp)
_AUDIO_MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/ogg", "pcm": "audio/pcm"}

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


def configure_http_mode(base_url: str, api_key: str) -> None:
    """Called by the speech server when mounting /mcp: tools call its own REST API."""
    global HTTP_MODE, API_URL, API_KEY
    HTTP_MODE = True
    API_URL = base_url.rstrip("/")
    API_KEY = (api_key or "").strip()


def _headers() -> dict[str, str]:
    key = API_KEY
    if not key and not HTTP_MODE and not os.environ.get("VIZIPVOICE_API_URL"):
        key = config.load().api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


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
) -> list[ContentBlock]:
    """Render Vietnamese text to speech.

    Over stdio the audio is saved to a local file (JSON with path, size, duration, history_id).
    Over HTTP the audio comes back inline as audio content (up to 8 MB); the JSON text block
    carries the history_id and the authenticated download path /v1/history/{id}/audio.
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
    history_id = resp.headers.get("x-tqd-history-id")
    download_path = f"/v1/history/{history_id}/audio" if history_id else None

    result: dict[str, Any] = {
        "status": "success",
        "voice": voice_id,
        "format": response_format,
        "sample_rate": sample_rate,
        "size_bytes": len(content),
        "duration_seconds": _audio_duration_seconds(content, response_format, sample_rate),
        "history_id": history_id,
        "download_path": download_path,
    }
    if HTTP_MODE:
        blocks: list[ContentBlock] = []
        if len(content) <= INLINE_AUDIO_LIMIT:
            blocks.append(
                AudioContent(
                    type="audio",
                    data=base64.b64encode(content).decode("ascii"),
                    mime_type=_AUDIO_MIME.get(response_format, "application/octet-stream"),
                )
            )
        else:
            result["note"] = "Âm thanh quá lớn để trả inline; tải qua download_path (cần Bearer)."
        blocks.append(TextContent(type="text", text=json.dumps(result, ensure_ascii=False)))
        return blocks

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"vizipvoice-{uuid.uuid4().hex[:12]}.{response_format}"
    out_path.write_bytes(content)
    result["path"] = str(out_path)
    if return_base64:
        result["audio_base64"] = base64.b64encode(content).decode("ascii")
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


@mcp.tool()
async def create_voice(
    name: str,
    reference_audio_path: Optional[str] = None,
    ref_text: Optional[str] = None,
    gender: Optional[str] = None,
    region: Optional[str] = None,
    description: str = "",
    denoise: bool = True,
    force: bool = False,
    consent: bool = False,
    audio_base64: Optional[str] = None,
    filename: str = "reference.wav",
) -> str:
    """Enroll a new voice from a reference clip (3-10s, wav/mp3/flac).

    Give the clip as `reference_audio_path` (stdio only, a file on the machine running the MCP
    process) or as `audio_base64` + `filename` (works over HTTP too; up to 25 MB).

    `ref_text` is the exact transcript of the clip; when omitted the server
    transcribes it. `gender` is male|female, `region` is bac|trung|nam.
    `consent` must be true: only clone a voice when its owner agreed.
    """
    if not consent:
        raise ValueError("Cần consent=true: chỉ clone giọng khi người đó đồng ý.")
    if audio_base64:
        try:
            audio_bytes = base64.b64decode(audio_base64, validate=False)
        except Exception as exc:
            raise ValueError("audio_base64 không hợp lệ") from exc
        upload_name = Path(filename).name or "reference.wav"
    elif reference_audio_path:
        if HTTP_MODE:
            raise ValueError("Qua HTTP không đọc được đường dẫn file: hãy gửi audio_base64 + filename.")
        ref_path = Path(reference_audio_path).expanduser()
        if not ref_path.is_file():
            raise ValueError(f"reference audio not found: {ref_path}")
        audio_bytes = ref_path.read_bytes()
        upload_name = ref_path.name
    else:
        raise ValueError("Cần reference_audio_path (stdio) hoặc audio_base64 + filename.")
    if len(audio_bytes) > MAX_REFERENCE_BYTES:
        raise ValueError("reference audio exceeds 25 MB limit")
    if ref_text is not None and not ref_text.strip():
        ref_text = None

    form = {
        "name": name,
        "description": description,
        "denoise": "true" if denoise else "false",
        "force": "true" if force else "false",
        "consent": "true",
    }
    if ref_text is not None:
        form["ref_text"] = ref_text
    if gender:
        form["gender"] = gender
    if region:
        form["region"] = region

    log.info("create_voice name=%s ref=%s", name, upload_name)
    resp = await _request(
        "POST",
        "/v1/voices",
        files={"file": (upload_name, audio_bytes, "application/octet-stream")},
        data=form,
    )
    return json.dumps({"status": "success", "voice": resp.json()}, ensure_ascii=False)


@mcp.tool()
async def create_job(
    text: str,
    voice_id: Optional[str] = None,
    response_format: str = "wav",
    speed: Optional[float] = None,
    normalize: bool = False,
    trim_silence: bool = False,
    music_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> str:
    """Submit a long text as a background job; poll with job_status. Returns {id, status}."""
    if not text.strip():
        raise ValueError("text must not be empty")
    payload: dict[str, Any] = {
        "input": text,
        "voice": voice_id or config.load().default_voice,
        "response_format": response_format,
        "sample_rate": 48000,
        "normalize": normalize,
        "trim_silence": trim_silence,
    }
    if speed is not None:
        payload["speed"] = speed
    if music_id:
        payload["music"] = {"id": music_id}
    if webhook_url:
        payload["webhook_url"] = webhook_url
    resp = await _request("POST", "/v1/jobs", json=payload)
    return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool()
async def job_status(job_id: str, save_audio: bool = False) -> str:
    """Status of a job (progress, urls). With save_audio=true and a finished job, also saves the audio file."""
    resp = await _request("GET", f"/v1/jobs/{job_id}")
    info = resp.json()
    if save_audio and not HTTP_MODE and info.get("status") == "done":
        audio = await _request("GET", info["audio_url"])
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ext = "wav" if audio.headers.get("content-type", "").endswith("wav") else "bin"
        out_path = OUTPUT_DIR / f"job-{job_id}.{ext}"
        out_path.write_bytes(audio.content)
        info["path"] = str(out_path)
    return json.dumps(info, ensure_ascii=False)


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
