"""tqd-voicestudio command line: serve | say | clone | voices | stop."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
import sys
from pathlib import Path

from tqd_voicestudio import config, daemon, netsec, paths


def _error_detail(resp) -> str:
    try:
        body = resp.json()
        return str(body.get("detail", body)) if isinstance(body, dict) else str(body)
    except Exception:
        return resp.text


def _load_env_file(path) -> None:
    with open(path, encoding="utf-8") as f:
        os.environ.update(json.load(f))
    entries = [e for e in os.environ.get("PYTHONPATH", "").split(os.pathsep) if e]
    new = list(dict.fromkeys(e for e in entries if e not in sys.path))
    sys.path[:0] = new


def _redirect_output(path) -> None:
    log = open(path, "ab", buffering=0)
    for fd in (1, 2):
        os.dup2(log.fileno(), fd)
    sys.stdout = open(1, "w", encoding="utf-8", buffering=1, closefd=False)
    sys.stderr = open(2, "w", encoding="utf-8", buffering=1, closefd=False)


def _cmd_serve(args) -> int:
    if args.env_file:
        _load_env_file(args.env_file)
    if args.log:
        _redirect_output(args.log)
    cfg = config.load()
    if args.port:
        cfg.port = args.port
    if getattr(args, "host", None):
        cfg.host = args.host
    if netsec.requires_key(cfg.host, cfg.api_key):
        print(netsec.REFUSE_MESSAGE.format(host=cfg.host), file=sys.stderr)
        return 2
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


def _say_body(args, cfg) -> dict:
    body = {
        "input": _read_text(args),
        "voice": args.voice or cfg.default_voice,
        "response_format": args.format,
    }
    if args.speed is not None:
        body["speed"] = args.speed
    if args.normalize:
        body["normalize"] = True
    if args.trim_silence:
        body["trim_silence"] = True
    if args.music:
        body["music"] = {"id": args.music}
    return body


def _poll_job(httpx, base: str, jid: str, headers: dict) -> dict:
    last = ""
    while True:
        info = httpx.get(f"{base}/v1/jobs/{jid}", headers=headers, timeout=60).json()
        line = f"job {jid}: {info['status']} {info['segments_done']}/{info['segments_total']} ({info['progress']:.0%})"
        if line != last:
            print(line, file=sys.stderr)
            last = line
        if info["status"] in ("done", "failed", "canceled"):
            return info
        time.sleep(1.0)


def _cmd_say(args) -> int:
    import httpx

    cfg = config.load()
    body = _say_body(args, cfg)
    base = daemon.ensure_server(cfg)
    if args.job:
        resp = httpx.post(f"{base}/v1/jobs", json=body, headers=daemon.auth_headers(cfg), timeout=None)
        if resp.status_code != 202:
            print(_error_detail(resp), file=sys.stderr)
            return 1
        info = _poll_job(httpx, base, resp.json()["id"], daemon.auth_headers(cfg))
        if info["status"] != "done":
            print(info.get("error") or f"job {info['status']}", file=sys.stderr)
            return 1
        resp = httpx.get(f"{base}{info['audio_url']}", headers=daemon.auth_headers(cfg), timeout=None)
    else:
        resp = httpx.post(f"{base}/v1/audio/speech", json=body, headers=daemon.auth_headers(cfg), timeout=None)
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

    if not args.consent:
        print("Cần --consent: chỉ clone giọng khi người đó đồng ý.", file=sys.stderr)
        return 2
    cfg = config.load()
    base = daemon.ensure_server(cfg)
    audio = Path(args.audio)
    data = {"name": args.name, "force": "true" if args.force else "false", "consent": "true"}
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
            headers=daemon.auth_headers(cfg),
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
    resp = httpx.get(f"{base}/v1/voices", params=params, headers=daemon.auth_headers(cfg), timeout=60)
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


def _cmd_jobs(args) -> int:
    import httpx

    cfg = config.load()
    base = daemon.ensure_server(cfg)
    action = args.action or "list"
    if action == "list":
        resp = httpx.get(f"{base}/v1/jobs", headers=daemon.auth_headers(cfg), timeout=60)
    elif action in ("show", "cancel"):
        if not args.job_id:
            print(f"jobs {action} cần JOB_ID", file=sys.stderr)
            return 2
        if action == "show":
            resp = httpx.get(f"{base}/v1/jobs/{args.job_id}", headers=daemon.auth_headers(cfg), timeout=60)
        else:
            resp = httpx.post(f"{base}/v1/jobs/{args.job_id}/cancel", headers=daemon.auth_headers(cfg), timeout=60)
    else:
        print("action phải là list, show hoặc cancel", file=sys.stderr)
        return 2
    if resp.status_code != 200:
        print(_error_detail(resp), file=sys.stderr)
        return 1
    payload = resp.json()
    if action == "list":
        for j in payload["data"]:
            print(f"{j['id']}  {j['status']:<9} {j['segments_done']}/{j['segments_total']}")
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_import(args) -> int:
    import httpx

    cfg = config.load()
    base = daemon.ensure_server(cfg)
    source = args.source
    if source.lower().startswith(("http://", "https://")):
        resp = httpx.post(f"{base}/v1/import", json={"url": source}, headers=daemon.auth_headers(cfg), timeout=120)
    else:
        path = Path(source)
        with open(path, "rb") as f:
            resp = httpx.post(f"{base}/v1/import", files={"file": (path.name, f)}, headers=daemon.auth_headers(cfg), timeout=120)
    if resp.status_code != 200:
        print(_error_detail(resp), file=sys.stderr)
        return 1
    data = resp.json()
    if args.output:
        Path(args.output).write_text(data["text"], encoding="utf-8")
        print(Path(args.output).resolve())
        print(f"{data['title']} ({data['words']} từ)", file=sys.stderr)
    else:
        print(data["text"])
    return 0


def _cmd_key(args) -> int:
    if args.which == "webhook":
        print(config.ensure_webhook_secret())
        return 0
    if args.which == "new":
        print(config.generate_api_key())
        print(
            "Đã lưu key vào config.toml (chỉ hiện một lần). Khởi động lại server để áp dụng: "
            "tqd-voicestudio stop rồi chạy lại lệnh bạn dùng.",
            file=sys.stderr,
        )
        return 0
    key = config.load().api_key
    if not key:
        print("Chưa có API key. Tạo bằng: tqd-voicestudio key new", file=sys.stderr)
        return 1
    print(key)
    return 0


def _cmd_stop(args) -> int:
    cfg = config.load()
    print("stopped" if daemon.stop_server(cfg) else "not running")
    return 0


def _cmd_mcp(args) -> int:
    import mcp_server

    mcp_server.main()
    return 0


def _cmd_mcp_config(args) -> int:
    from tqd_voicestudio import mcp_config

    return mcp_config.run(args)


def _cmd_doctor(args) -> int:
    from tqd_voicestudio import doctor

    return doctor.run(args, config.load())


def _cmd_install(args) -> int:
    from tqd_voicestudio import install

    return install.run(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tqd-voicestudio", description="TQD-VoiceStudio: Vietnamese TTS + voice cloning")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local server in the foreground")
    s.add_argument("--port", type=int)
    s.add_argument("--host", help="địa chỉ bind (mặc định theo config; khác loopback cần API key)")
    s.add_argument("--env-file", help=argparse.SUPPRESS)
    s.add_argument("--log", help=argparse.SUPPRESS)
    s.set_defaults(fn=_cmd_serve)

    s = sub.add_parser("say", help="synthesize speech to a file")
    s.add_argument("text", nargs="?", help="text, or - for stdin")
    s.add_argument("-f", "--file", help="read text from a UTF-8 file")
    s.add_argument("-v", "--voice")
    s.add_argument("-o", "--output")
    s.add_argument("--format", choices=["wav", "mp3", "flac", "opus"], default="wav")
    s.add_argument("--speed", type=float)
    s.add_argument("--normalize", action="store_true", help="chuẩn hóa âm lượng về -16 LUFS")
    s.add_argument("--trim-silence", action="store_true", help="rút ngắn khoảng lặng dài hơn 0,6 giây")
    s.add_argument("--music", metavar="ID", help="id nhạc nền đã tải lên (POST /v1/music)")
    s.add_argument("--job", action="store_true", help="gửi thành job nền và theo dõi tiến độ")
    s.set_defaults(fn=_cmd_say)

    s = sub.add_parser("clone", help="enroll a voice from a reference clip")
    s.add_argument("name")
    s.add_argument("audio")
    s.add_argument("--text")
    s.add_argument("--gender", choices=["male", "female"])
    s.add_argument("--region", choices=["bac", "trung", "nam"])
    s.add_argument("--force", action="store_true")
    s.add_argument("--consent", action="store_true", help="xác nhận người có giọng đã đồng ý cho clone")
    s.set_defaults(fn=_cmd_clone)

    s = sub.add_parser("voices", help="list voices")
    s.add_argument("--gender")
    s.add_argument("--region")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_cmd_voices)

    s = sub.add_parser("doctor", help="analyse this machine and recommend a config")
    s.add_argument("--json", action="store_true")
    s.add_argument("--bench", action="store_true", help="time one synthesis")
    s.set_defaults(fn=_cmd_doctor)

    s = sub.add_parser("install", help="download models, write config, smoke test")
    s.add_argument("--no-asr", action="store_true")
    s.add_argument("--no-smoke", action="store_true")
    s.add_argument("--voices", choices=["vivos"], help="cài thêm gói giọng VIVOS (CC BY-NC-SA 4.0)")
    s.add_argument("--yes", "-y", action="store_true", help="đồng ý giấy phép, không hỏi")
    s.set_defaults(fn=_cmd_install)

    s = sub.add_parser("mcp", help="run the MCP stdio server (uses the local daemon)")
    s.set_defaults(fn=_cmd_mcp)

    s = sub.add_parser("mcp-config", help="print MCP client configuration")
    s.add_argument("--client", choices=["claude-desktop", "cursor", "claude-code"], default="claude-desktop")
    s.add_argument("--remote", metavar="URL", help="URL server từ xa (ví dụ http://100.x.y.z:8765): in cấu hình HTTP")
    s.add_argument("--key-env", metavar="NAME", help="dùng biến môi trường NAME thay cho <KEY>")
    s.add_argument("--show-key", action="store_true", help="in key thật từ config cục bộ")
    s.set_defaults(fn=_cmd_mcp_config)

    s = sub.add_parser("jobs", help="liệt kê / xem / hủy job")
    s.add_argument("action", nargs="?", choices=["list", "show", "cancel"], default="list")
    s.add_argument("job_id", nargs="?")
    s.set_defaults(fn=_cmd_jobs)

    s = sub.add_parser("import", help="nhập văn bản từ file (.txt .md .docx .pdf) hoặc URL")
    s.add_argument("source", metavar="FILE_OR_URL")
    s.add_argument("-o", "--output", help="ghi văn bản ra file")
    s.set_defaults(fn=_cmd_import)

    s = sub.add_parser("key", help="khóa bí mật")
    s.add_argument("which", choices=["new", "show", "webhook"], help="new: tạo API key; show: in key; webhook: in secret ký webhook")
    s.set_defaults(fn=_cmd_key)

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
