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


def test_macos_arm64_mps_recommends_mps():
    f = facts(
        os="Darwin", arch="arm64", cuda_available=False, gpu_name=None, vram_total_gb=None,
        nvidia_smi=False, torch_cuda=None, mps_available=True,
    )
    cfg, checks = recommend(f, Config())
    assert cfg.device == "mps" and cfg.fp16 is False and cfg.max_streams == 1
    warns = [c for c in checks if c.level == "warn"]
    assert any("THỬ NGHIỆM" in c.message and 'device = "cpu"' in c.message for c in warns)
    assert not any("NVIDIA" in c.message or "cuda" in c.message.lower() for c in checks)


def test_macos_intel_or_no_mps_recommends_cpu():
    f = facts(
        os="Darwin", arch="x86_64", cuda_available=False, gpu_name=None, vram_total_gb=None,
        nvidia_smi=False, torch_cuda=None, mps_available=False,
    )
    cfg, checks = recommend(f, Config())
    assert cfg.device == "cpu" and cfg.fp16 is False and cfg.max_streams == 1
    assert not any(c.level == "error" for c in checks)
    assert not any("NVIDIA" in c.message for c in checks)


def test_macos_arm64_without_mps_recommends_cpu():
    f = facts(
        os="Darwin", arch="arm64", cuda_available=False, gpu_name=None, vram_total_gb=None,
        nvidia_smi=False, torch_cuda=None, mps_available=False,
    )
    cfg, _ = recommend(f, Config())
    assert cfg.device == "cpu"


def test_missing_ffmpeg_fix_macos():
    _, mac = recommend(facts(ffmpeg=None, os="Darwin"), Config())
    assert any("brew install ffmpeg" in c.fix for c in mac)


def test_collect_includes_mps(monkeypatch):
    import tqd_voicestudio.doctor as doctor

    monkeypatch.setattr(doctor, "_driver_version", lambda: (False, None))
    monkeypatch.setattr(doctor, "_port_state", lambda cfg: "free")
    monkeypatch.setattr(doctor, "_disk_free_gb", lambda: 100.0)
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    f = doctor.collect(Config())
    assert f.mps_available is True


def test_env_device_override_cpu_on_macos(monkeypatch):
    monkeypatch.setenv("VIZIPVOICE_DEVICE", "cpu")
    f = facts(
        os="Darwin", arch="arm64", cuda_available=False, gpu_name=None, vram_total_gb=None,
        nvidia_smi=False, torch_cuda=None, mps_available=True,
    )
    cfg, checks = recommend(f, Config())
    assert cfg.device == "cpu" and cfg.fp16 is False
    assert any("VIZIPVOICE_DEVICE" in c.message for c in checks)


def test_env_device_override_mps(monkeypatch):
    monkeypatch.setenv("VIZIPVOICE_DEVICE", "mps")
    cfg, _ = recommend(facts(cuda_available=True), Config())
    assert cfg.device == "mps" and cfg.fp16 is False and cfg.max_streams == 1


def test_no_env_override_by_default(monkeypatch):
    monkeypatch.delenv("VIZIPVOICE_DEVICE", raising=False)
    cfg, _ = recommend(facts(), Config())
    assert cfg.device == "cuda"
