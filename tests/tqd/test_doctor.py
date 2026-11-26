from dataclasses import replace

from tqd_voicestudio.config import Config
from tqd_voicestudio.doctor import Facts, recommend


def facts(**kw) -> Facts:
    base = Facts(
        os="Windows", arch="AMD64", python="3.12.0", torch_version="2.8.0", torch_cuda="12.8",
        cuda_available=True, gpu_name="RTX 3060", vram_total_gb=12.0, nvidia_smi=True,
        driver_version="570.0", ram_total_gb=32.0, ram_avail_gb=20.0, disk_free_gb=100.0,
        ffmpeg="ffmpeg", port=8765, port_state="free", model_present=True,
    )
    return replace(base, **kw)


def levels(checks):
    return {c.level for c in checks}


def test_12gb_gpu():
    cfg, checks = recommend(facts(), Config())
    assert cfg.device == "cuda" and cfg.max_streams == 4
    assert "error" not in levels(checks)


def test_8gb_gpu():
    cfg, _ = recommend(facts(vram_total_gb=8.0), Config())
    assert cfg.max_streams == 2


def test_5gb_gpu():
    cfg, checks = recommend(facts(vram_total_gb=5.0), Config())
    assert cfg.max_streams == 1 and "warn" in levels(checks)


def test_3gb_gpu_falls_back_to_cpu():
    cfg, _ = recommend(facts(vram_total_gb=3.0), Config())
    assert cfg.device == "cpu"


def test_cpu_torch_on_nvidia_machine():
    _, checks = recommend(
        facts(cuda_available=False, torch_cuda=None, gpu_name=None, vram_total_gb=None), Config()
    )
    errs = [c for c in checks if c.level == "error"]
    assert errs and "--torch-backend=auto" in errs[0].fix


def test_no_gpu():
    cfg, checks = recommend(
        facts(cuda_available=False, nvidia_smi=False, torch_cuda=None, gpu_name=None, vram_total_gb=None),
        Config(),
    )
    assert cfg.device == "cpu" and cfg.fp16 is False and "warn" in levels(checks)


def test_low_disk():
    _, checks = recommend(facts(disk_free_gb=2.0), Config())
    assert "error" in levels(checks)


def test_missing_ffmpeg_fix_per_os():
    _, win = recommend(facts(ffmpeg=None, os="Windows"), Config())
    _, lin = recommend(facts(ffmpeg=None, os="Linux"), Config())
    assert any("winget" in c.fix for c in win)
    assert any("apt" in c.fix for c in lin)
