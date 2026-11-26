#!/usr/bin/env python3
"""End-to-end MCP stdio smoke test: spawn `<exe> mcp`, list tools, call list_voices + synthesize_speech.

  python scripts/smoke/mcp_smoke.py --exe D:/TTS/ViZipvoice/.venv/Scripts/tqd-voicestudio.exe

Uses the installed `mcp` client API: mcp.Client over mcp.client.stdio.StdioServerParameters
(Client.list_tools / Client.call_tool). The full current environment is passed to the child
because StdioServerParameters.env is merged over a minimal default environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

REQUIRED_TOOLS = {"list_voices", "synthesize_speech", "create_voice"}
TEXT = "Xin chào, đây là thử nghiệm MCP của TQD VoiceStudio."
TOTAL_TIMEOUT_S = 400


def _text(result) -> str:
    if result.is_error:
        raise RuntimeError("tool error: " + " ".join(getattr(b, "text", "") for b in result.content))
    return "".join(getattr(b, "text", "") for b in result.content)


async def run(exe: str) -> str:
    params = StdioServerParameters(command=exe, args=["mcp"], env=dict(os.environ))
    async with Client(params) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        missing = REQUIRED_TOOLS - tools
        if missing:
            raise RuntimeError(f"missing tools: {sorted(missing)}")

        voices = _text(await client.call_tool("list_voices", {}))
        if "Đinh-Quyết" not in voices:
            raise RuntimeError("list_voices result does not contain Đinh-Quyết")

        out = json.loads(_text(await client.call_tool("synthesize_speech", {"text": TEXT})))
        path = Path(out["path"])
        if not path.is_file():
            raise RuntimeError(f"output file missing: {path}")
        if out["size_bytes"] <= 10000:
            raise RuntimeError(f"output too small: {out['size_bytes']} bytes")
        return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exe", default=shutil.which("tqd-voicestudio"))
    args = ap.parse_args()
    if not args.exe:
        print("tqd-voicestudio executable not found; pass --exe", file=sys.stderr)
        return 1
    try:
        path = asyncio.run(asyncio.wait_for(run(args.exe), TOTAL_TIMEOUT_S))
    except Exception as exc:
        print(f"MCP_SMOKE_FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"MCP_SMOKE_OK {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
