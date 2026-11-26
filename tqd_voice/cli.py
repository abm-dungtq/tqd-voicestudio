"""tqd-voice command line: serve | say | clone | voices | stop."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from tqd_voice import config, daemon, paths


def _error_detail(resp) -> str:
    try:
        body = resp.json()
        return str(body.get("detail", body)) if isinstance(body, dict) else str(body)
    except Exception:
        return resp.text


def _cmd_serve(args) -> int:
    cfg = config.load()
    if args.port:
        cfg.port = args.port
    os.environ.update(config.to_env(cfg))
    import vizipvoice_openai_server

    vizipvoice_openai_server.main()
    return 0


def _read_text(args) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.text == "-":
        return sys.stdin.read()
    return args.text


def _cmd_say(args) -> int:
    import httpx

    cfg = config.load()
    text = _read_text(args)
    base = daemon.ensure_server(cfg)
    body = {
        "input": text,
        "voice": args.voice or cfg.default_voice,
        "response_format": args.format,
    }
    if args.speed is not None:
        body["speed"] = args.speed
    resp = httpx.post(f"{base}/v1/audio/speech", json=body, timeout=None)
    if resp.status_code != 200:
        print(_error_detail(resp), file=sys.stderr)
        return 1
    if args.output:
        out = Path(args.output)
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = paths.outputs_dir() / f"tqd-{stamp}.{args.format}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    print(out.resolve())
    return 0


def _cmd_clone(args) -> int:
    import httpx

    cfg = config.load()
    base = daemon.ensure_server(cfg)
    audio = Path(args.audio)
    data = {"name": args.name, "force": "true" if args.force else "false"}
    if args.text:
        data["prompt_text"] = args.text
    if args.gender:
        data["gender"] = args.gender
    if args.region:
        data["region"] = args.region
    with open(audio, "rb") as f:
        resp = httpx.post(
            f"{base}/v1/voices",
            data=data,
            files={"file": (audio.name, f)},
            timeout=None,
        )
    if not (200 <= resp.status_code < 300):
        print(_error_detail(resp), file=sys.stderr)
        return 1
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
    return 0


def _cmd_voices(args) -> int:
    import httpx

    cfg = config.load()
    base = daemon.ensure_server(cfg)
    params = {}
    if args.gender:
        params["gender"] = args.gender
    if args.region:
        params["region"] = args.region
    resp = httpx.get(f"{base}/v1/voices", params=params, timeout=60)
    if resp.status_code != 200:
        print(_error_detail(resp), file=sys.stderr)
        return 1
    payload = resp.json()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    cols = ("name", "gender", "region", "kind")
    rows = [[str(v.get(c) or "") for c in cols] for v in payload.get("data", [])]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(cols))))
    return 0


def _cmd_stop(args) -> int:
    cfg = config.load()
    print("stopped" if daemon.stop_server(cfg) else "not running")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tqd-voice", description="TQD-Voice: Vietnamese TTS + voice cloning")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local server in the foreground")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=_cmd_serve)

    s = sub.add_parser("say", help="synthesize speech to a file")
    s.add_argument("text", nargs="?", help="text, or - for stdin")
    s.add_argument("-f", "--file", help="read text from a UTF-8 file")
    s.add_argument("-v", "--voice")
    s.add_argument("-o", "--output")
    s.add_argument("--format", choices=["wav", "mp3", "flac", "opus"], default="wav")
    s.add_argument("--speed", type=float)
    s.set_defaults(fn=_cmd_say)

    s = sub.add_parser("clone", help="enroll a voice from a reference clip")
    s.add_argument("name")
    s.add_argument("audio")
    s.add_argument("--text")
    s.add_argument("--gender", choices=["male", "female"])
    s.add_argument("--region", choices=["bac", "trung", "nam"])
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=_cmd_clone)

    s = sub.add_parser("voices", help="list voices")
    s.add_argument("--gender")
    s.add_argument("--region")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_cmd_voices)

    s = sub.add_parser("stop", help="stop the background server")
    s.set_defaults(fn=_cmd_stop)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "say" and not args.text and not args.file:
        parser.error("say needs TEXT, -f FILE, or - for stdin")
    try:
        return args.fn(args)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
