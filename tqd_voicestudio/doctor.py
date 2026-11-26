"""Machine analysis (doctor): collect facts, recommend a config, print a report."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace

from tqd_voicestudio import daemon, paths
from tqd_voicestudio.config import Config

BENCH_TEXT = (
    "Đọc sách là một thói quen tốt, nhưng ngày càng ít người duy trì được trong "
    "nhịp sống hiện đại, khi điện thoại chiếm gần hết thời gian rảnh."
)


@dataclass
class Facts:
    os: str
    arch: str
    python: str
    torch_version: str
    torch_cuda: str | None
    cuda_available: bool
    gpu_name: str | None
    vram_total_gb: float | None
    nvidia_smi: bool
    driver_version: str | None
    ram_total_gb: float
    ram_avail_gb: float
    disk_free_gb: float
    ffmpeg: str | None
    port: int
    port_state: str  # "free" | "tqd" | "other"
    model_present: bool
    mps_available: bool = False


@dataclass
class Check:
    level: str  # ok | warn | error
    message: str
    fix: str = ""


def _driver_version() -> tuple[bool, str | None]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        line = out.stdout.strip().splitlines()
        return True, (line[0].strip() if out.returncode == 0 and line else None)
    except (subprocess.SubprocessError, OSError):
        return True, None


def _disk_free_gb() -> float:
    target = paths.home()
    while not target.exists() and target != target.parent:
        target = target.parent
    return shutil.disk_usage(target).free / 1024**3


def _port_state(cfg: Config) -> str:
    info = daemon.health(cfg)
    if info is not None and "voices" in info:
        return "tqd"
    if info is not None:
        return "other"
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", cfg.port))
        return "free"
    except OSError:
        return "other"


def collect(cfg: Config) -> Facts:
    import psutil
    import torch

    cuda = bool(torch.cuda.is_available())
    gpu_name = vram = None
    if cuda:
        gpu_name = torch.cuda.get_device_name(0)
        vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    smi, driver = _driver_version()
    mem = psutil.virtual_memory()
    return Facts(
        os=platform.system(),
        arch=platform.machine(),
        python=platform.python_version(),
        torch_version=torch.__version__,
        torch_cuda=torch.version.cuda,
        cuda_available=cuda,
        gpu_name=gpu_name,
        vram_total_gb=vram,
        nvidia_smi=smi,
        driver_version=driver,
        ram_total_gb=mem.total / 1024**3,
        ram_avail_gb=mem.available / 1024**3,
        disk_free_gb=_disk_free_gb(),
        ffmpeg=shutil.which("ffmpeg"),
        port=cfg.port,
        port_state=_port_state(cfg),
        model_present=(paths.model_dir() / "checkpoint-1860000.pt").is_file(),
        mps_available=bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
    )


def recommend(f: Facts, base: Config) -> tuple[Config, list[Check]]:
    cfg = replace(base)
    checks: list[Check] = []
    forced = os.environ.get("VIZIPVOICE_DEVICE", "").strip().lower()
    if forced in {"cpu", "cuda", "mps"}:
        # Explicit env override wins over auto-detection (useful on CI/VMs where
        # a GPU is visible but not usable, e.g. MPS in virtualized macOS runners).
        cfg.device = forced
        cfg.fp16 = forced == "cuda" and bool(f.cuda_available)
        if forced == "cuda":
            cfg.max_streams = 4 if (f.vram_total_gb or 0) >= 10 else 2
        else:
            cfg.max_streams = 1
        checks.append(Check("ok", f"VIZIPVOICE_DEVICE={forced}: ép thiết bị theo biến môi trường"))
    elif f.os == "Darwin":
        # macOS: never CUDA/NVIDIA advice. Apple Silicon with MPS → experimental mps.
        if f.arch == "arm64" and f.mps_available:
            cfg.device, cfg.fp16, cfg.max_streams = "mps", False, 1
            checks.append(Check(
                "warn",
                "Apple Silicon: dùng MPS (GPU) — TÍNH NĂNG THỬ NGHIỆM. "
                'MPS lỗi thì đặt device = "cpu" trong config.toml',
            ))
        else:
            cfg.device, cfg.fp16, cfg.max_streams = "cpu", False, 1
            checks.append(Check(
                "warn",
                "macOS không có MPS khả dụng: chạy CPU, chậm. "
                "Đo tốc độ bằng: tqd-voicestudio doctor --bench",
            ))
    elif f.cuda_available and (f.vram_total_gb or 0) >= 6:
        vram = f.vram_total_gb
        cfg.device, cfg.fp16 = "cuda", True
        cfg.max_streams = 4 if vram >= 10 else 2
        checks.append(Check("ok", f"GPU {f.gpu_name} {vram:.0f} GB"))
    elif f.cuda_available and (f.vram_total_gb or 0) >= 4:
        cfg.device, cfg.fp16, cfg.max_streams = "cuda", True, 1
        checks.append(Check("warn", "VRAM thấp, chỉ chạy 1 luồng"))
    elif f.cuda_available:
        cfg.device, cfg.fp16, cfg.max_streams = "cpu", False, 1
        checks.append(Check("warn", "VRAM dưới 4 GB, chuyển sang CPU"))
    elif f.nvidia_smi and f.torch_cuda is None:
        cfg.device, cfg.fp16, cfg.max_streams = "cpu", False, 1
        checks.append(Check(
            "error",
            f"Máy có GPU NVIDIA (driver {f.driver_version}) nhưng PyTorch là bản CPU",
            "uv tool install --reinstall --python 3.12 --torch-backend=auto "
            "tqd-voicestudio[import] @ git+https://github.com/abm-dungtq/tqd-voicestudio@v0.3.0",
        ))
    elif f.nvidia_smi:
        cfg.device, cfg.fp16, cfg.max_streams = "cpu", False, 1
        checks.append(Check(
            "error",
            f"PyTorch CUDA {f.torch_cuda} không chạy được với driver {f.driver_version}",
            "Cập nhật driver NVIDIA mới nhất, hoặc cài lại với --torch-backend=auto",
        ))
    else:
        cfg.device, cfg.fp16, cfg.max_streams = "cpu", False, 1
        checks.append(Check(
            "warn",
            "Không có GPU NVIDIA: chạy CPU, chậm. Đo tốc độ bằng: tqd-voicestudio doctor --bench",
        ))
    if f.disk_free_gb < 3:
        checks.append(Check("error", f"Ổ đĩa còn {f.disk_free_gb:.1f} GB, cần ít nhất 3 GB"))
    if f.ram_total_gb < 8:
        checks.append(Check("warn", f"RAM {f.ram_total_gb:.0f} GB, khuyến nghị ≥ 8 GB"))
    if f.ffmpeg is None:
        fix = {
            "Windows": "winget install --id Gyan.FFmpeg -e",
            "Linux": "sudo apt install ffmpeg",
            "Darwin": "brew install ffmpeg",
        }.get(f.os, "Cài ffmpeg và thêm vào PATH")
        checks.append(Check("warn", "Thiếu ffmpeg: chỉ xuất được wav", fix))
    if f.port_state == "other":
        checks.append(Check(
            "warn", f"Cổng {f.port} đang bị chương trình khác dùng",
            f"Sửa port trong {paths.config_path()}",
        ))
    if not f.model_present:
        checks.append(Check("warn", "Chưa có model", "tqd-voicestudio install"))
    return cfg, checks


_LABELS = {"ok": "[OK]", "warn": "[CẢNH BÁO]", "error": "[LỖI]"}


def print_report(f: Facts, checks: list[Check], cfg: Config, file=None) -> None:
    out = file or sys.stdout
    print("== Máy ==", file=out)
    for key, value in asdict(f).items():
        print(f"  {key}: {value}", file=out)
    print("== Kiểm tra ==", file=out)
    for c in checks:
        print(f"{_LABELS[c.level]} {c.message}", file=out)
        if c.fix:
            print(f"  → {c.fix}", file=out)
    print("== Cấu hình đề xuất ==", file=out)
    print(f"  device: {cfg.device}", file=out)
    print(f"  fp16: {cfg.fp16}", file=out)
    print(f"  max_streams: {cfg.max_streams}", file=out)


def bench(cfg: Config) -> str:
    import httpx

    base = daemon.ensure_server(cfg)
    t0 = time.perf_counter()
    resp = httpx.post(
        f"{base}/v1/audio/speech",
        headers=daemon.auth_headers(cfg),
        json={"input": BENCH_TEXT, "voice": cfg.default_voice,
              "response_format": "wav", "sample_rate": 24000},
        timeout=None,
    )
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        raise RuntimeError(f"bench failed: HTTP {resp.status_code} {resp.text[:200]}")
    import io

    import soundfile as sf

    info = sf.info(io.BytesIO(resp.content))
    audio_s = info.frames / info.samplerate
    return (f"Thời gian: {elapsed:.2f} s | Âm thanh: {audio_s:.2f} s | "
            f"Tốc độ: {audio_s / elapsed:.2f} lần thời gian thực")


def run(args, cfg: Config) -> int:
    facts = collect(cfg)
    rec, checks = recommend(facts, cfg)
    if args.json:
        print(json.dumps(
            {"facts": asdict(facts), "checks": [asdict(c) for c in checks], "recommended": asdict(rec)},
            ensure_ascii=False, indent=2,
        ))
    else:
        print_report(facts, checks, rec)
    if args.bench:
        print(bench(cfg))
    return 1 if any(c.level == "error" for c in checks) else 0
