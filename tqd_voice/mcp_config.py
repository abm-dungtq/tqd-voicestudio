"""`tqd-voice mcp-config`: print ready-made MCP client configuration."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def resolve_exe() -> str:
    exe = shutil.which("tqd-voice")
    path = Path(exe).absolute() if exe else Path(sys.argv[0]).resolve()
    if os.name == "nt" and not path.suffix:
        sibling = path.with_name(path.name + ".exe")
        if sibling.is_file():
            path = sibling
    return str(path)


def _desktop_config_path() -> str:
    if sys.platform == "win32":
        return r"%APPDATA%\Claude\claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


def render(client: str, exe: str) -> tuple[str, str]:
    """Return (stdout_text, stderr_note)."""
    if client == "claude-code":
        return f'claude mcp add tqd-voice -- "{exe}" mcp', ""
    body = json.dumps(
        {"mcpServers": {"tqd-voice": {"command": exe, "args": ["mcp"]}}},
        indent=2,
        ensure_ascii=False,
    )
    where = "~/.cursor/mcp.json" if client == "cursor" else _desktop_config_path()
    return body, f"Thêm đoạn JSON trên vào file cấu hình: {where}"


def run(args) -> int:
    text, note = render(args.client, resolve_exe())
    print(text)
    if note:
        print(note, file=sys.stderr)
    return 0
