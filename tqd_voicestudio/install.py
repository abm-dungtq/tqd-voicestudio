"""tqd-voicestudio install: download models, write config, smoke-test one sentence."""

from __future__ import annotations

import os
import sys

from tqd_voicestudio import config, daemon, doctor, mcp_config, paths

MODEL_REPO = "contextboxai/ViZipvoice"
MODEL_REVISION = "7903dcb99d5a17c2e59a0ef5f7db32de4bed357b"
VIVOS_REPO = "dungtq/tqd-voicestudio-vivos-voices"
VIVOS_REVISION = "1b47de396ee33b5a786bae000a1003e8da93d86d"
VIVOS_NOTICE = (
    "Gói 22 giọng (dungtq/tqd-voicestudio-vivos-voices), giấy phép gói CC BY-NC-SA 4.0: 17 giọng từ VIVOS "
    "(CC BY-NC-SA 4.0) và 5 giọng từ DNU-ViSpeech (CC BY-NC 4.0). Chỉ dùng học tập, phi thương mại; "
    "không mạo danh người thật."
)
SMOKE_TEXT = "Xin chào, TQD VoiceStudio đã cài đặt xong."


def _confirm(notice: str, assume_yes: bool) -> bool:
    print(notice)
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Không có terminal để xác nhận: thêm --yes để đồng ý với giấy phép trên.", file=sys.stderr)
        return False
    return input("Bạn đồng ý? [y/N] ").strip().lower() in ("y", "yes")


def run(args) -> int:
    want_vivos = getattr(args, "voices", None) == "vivos"
    assume_yes = bool(getattr(args, "yes", False))
    if want_vivos and not _confirm(VIVOS_NOTICE, assume_yes):
        print("Đã hủy: chưa đồng ý giấy phép.", file=sys.stderr)
        return 2
    cfg = config.load()
    facts = doctor.collect(cfg)
    rec, checks = doctor.recommend(facts, cfg)
    rec = config.merge_recommended(cfg, rec)
    doctor.print_report(facts, checks, rec)
    errors = [c for c in checks if c.level == "error"]
    if errors:
        for c in errors:
            print(f"Không thể cài: {c.message}", file=sys.stderr)
            if c.fix:
                print(f"  → {c.fix}", file=sys.stderr)
        return 1

    for d in (paths.home(), paths.voices_dir(), paths.outputs_dir(), paths.logs_dir(), paths.hf_cache()):
        d.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(paths.hf_cache())
    from huggingface_hub import snapshot_download

    extras_only = want_vivos and (paths.model_dir() / "checkpoint-1860000.pt").is_file()
    if not extras_only:
        print(f"Tải model {MODEL_REPO} → {paths.model_dir()}", file=sys.stderr)
        snapshot_download(
            MODEL_REPO,
            revision=MODEL_REVISION,
            local_dir=paths.model_dir(),
            allow_patterns=["checkpoint-1860000.pt", "config.json", "model.json", "tokens.txt", "audio/*"],
        )
        print("Tải vocoder charactr/vocos-mel-24khz", file=sys.stderr)
        # max_workers=1: hub 0.36 symlink-probe race on Windows without symlink privilege
        snapshot_download("charactr/vocos-mel-24khz", max_workers=1)
        if not args.no_asr:
            print("Tải ASR vinai/PhoWhisper-small", file=sys.stderr)
            snapshot_download("vinai/PhoWhisper-small", max_workers=1)

    if want_vivos:
        pack_dir = paths.home() / "packs" / "vivos"
        print(f"Tải gói giọng VIVOS {VIVOS_REPO} → {pack_dir}", file=sys.stderr)
        snapshot_download(
            VIVOS_REPO,
            repo_type="dataset",
            revision=VIVOS_REVISION,
            local_dir=pack_dir,
            allow_patterns=["library/*", "GIAY-PHEP.md", "voices.csv"],
        )
        rec.library_dir = str(pack_dir / "library")

    print(f"Đã ghi cấu hình: {config.save(rec)}")

    if not args.no_smoke:
        import httpx

        base = daemon.ensure_server(rec)
        try:
            resp = httpx.post(
                f"{base}/v1/audio/speech",
                headers=daemon.auth_headers(rec),
                json={"input": SMOKE_TEXT, "voice": rec.default_voice, "response_format": "wav"},
                timeout=None,
            )
            if resp.status_code != 200:
                print(f"Chạy thử thất bại: HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
                return 1
            out = paths.outputs_dir() / "hello.wav"
            out.write_bytes(resp.content)
            print(f"File thử: {out}")
        finally:
            daemon.stop_server(rec)

    print('Xong. Thử: tqd-voicestudio say "Xin chào"')
    text, note = mcp_config.render("claude-desktop", mcp_config.resolve_exe())
    print("Cấu hình MCP (Claude Desktop):")
    print(text)
    if note:
        print(note)
    return 0
