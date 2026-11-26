"""`tqd-voicestudio mcp-config`: print ready-made MCP client configuration."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from tqd_voicestudio import config

KEY_PLACEHOLDER = "<KEY>"


def resolve_exe() -> str:
    exe = shutil.which("tqd-voicestudio")
    path = Path(exe).absolute() if exe else Path(sys.argv[0]).resolve()
    if os.name == "nt" and not path.suffix:
        sibling = path.with_name(path.name + ".exe")
        if sibling.is_file():
            path = sibling
    return str(path)


def _desktop_config_path() -> str:
    if sys.platform == "win32":
        return r"%APPDATA%\Claude\claude_desktop_config.json"
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


def render(client: str, exe: str) -> tuple[str, str]:
    """Return (stdout_text, stderr_note)."""
    if client == "claude-code":
        return f'claude mcp add tqd-voicestudio -- "{exe}" mcp', ""
    body = json.dumps(
        {"mcpServers": {"tqd-voicestudio": {"command": exe, "args": ["mcp"]}}},
        indent=2,
        ensure_ascii=False,
    )
    where = "~/.cursor/mcp.json" if client == "cursor" else _desktop_config_path()
    return body, f"Thêm đoạn JSON trên vào file cấu hình: {where}"


def _split_remote(url: str) -> tuple[str, str]:
    base = url.strip().rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return base, base + "/mcp"


def render_remote(url: str, exe: str, key: str = KEY_PLACEHOLDER, key_env: Optional[str] = None) -> str:
    """Three remote setups: Claude Code (HTTP), Cursor (HTTP), Claude Desktop (stdio bridge)."""
    base, mcp_url = _split_remote(url)
    if key_env:
        cc_key, cursor_key, bridge_key = f"${{{key_env}}}", f"${{env:{key_env}}}", KEY_PLACEHOLDER
    else:
        cc_key = cursor_key = bridge_key = key
    cursor = json.dumps(
        {"mcpServers": {"tqd-voicestudio": {"url": mcp_url, "headers": {"Authorization": f"Bearer {cursor_key}"}}}},
        indent=2, ensure_ascii=False,
    )
    bridge = json.dumps(
        {"mcpServers": {"tqd-voicestudio": {
            "command": exe, "args": ["mcp"],
            "env": {"VIZIPVOICE_API_URL": base, "VIZIPVOICE_API_KEY": bridge_key},
        }}},
        indent=2, ensure_ascii=False,
    )
    return "\n".join([
        "# 1) Claude Code (HTTP)",
        f'claude mcp add --transport http tqd-voicestudio {mcp_url} --header "Authorization: Bearer {cc_key}"',
        "",
        "# 2) Cursor (~/.cursor/mcp.json)",
        cursor,
        "",
        "# 3) Claude Desktop (cầu nối stdio, thêm vào claude_desktop_config.json)",
        bridge,
    ])


def run(args) -> int:
    remote = getattr(args, "remote", None)
    if remote:
        key = KEY_PLACEHOLDER
        if getattr(args, "show_key", False):
            key = config.load().api_key
            if not key:
                print("Chưa có API key. Tạo bằng: tqd-voicestudio key new", file=sys.stderr)
                return 1
        print(render_remote(remote, resolve_exe(), key=key, key_env=getattr(args, "key_env", None)))
        if key == KEY_PLACEHOLDER and not getattr(args, "key_env", None):
            print("Thay <KEY> bằng API key (tqd-voicestudio key show), hoặc dùng --key-env / --show-key.",
                  file=sys.stderr)
        return 0
    text, note = render(args.client, resolve_exe())
    print(text)
    if note:
        print(note, file=sys.stderr)
    return 0
