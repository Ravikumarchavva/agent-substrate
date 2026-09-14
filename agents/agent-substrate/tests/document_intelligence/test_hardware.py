"""Unit tests for hardware.py's detection chain.

These tests monkeypatch each detection method absent/failing in turn and
assert detect() never raises and always returns a usable HardwareProfile.
"""

from __future__ import annotations

import builtins

import pytest

from substrate.runtimes.document_intelligence.service import hardware


@pytest.fixture(autouse=True)
def _no_cuda_visible_devices_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure a clean baseline unless a test explicitly sets it.
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)


def test_detect_never_raises_with_everything_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "_detect_gpus_pynvml", lambda: None)
    monkeypatch.setattr(hardware, "_detect_gpus_nvidia_smi", lambda: None)
    monkeypatch.setattr(hardware, "_detect_gpus_paddle", lambda: None)

    profile = hardware.detect()

    assert isinstance(profile, hardware.HardwareProfile)
    assert profile.gpus == []
    assert profile.source == "none"
    assert profile.cpu_count >= 1


def test_cuda_visible_devices_empty_string_means_zero_gpus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")

    # Even if pynvml would report GPUs, the override should short-circuit.
    monkeypatch.setattr(
        hardware,
        "_detect_gpus_pynvml",
        lambda: [hardware.GpuInfo(index=0, name="fake", total_mib=8192, free_mib=8192)],
    )

    profile = hardware.detect()

    assert profile.gpus == []
    assert profile.source == "cuda_visible_devices_override"


def test_pynvml_not_installed_falls_back_to_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pynvml":
            raise ImportError("no pynvml")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.setattr(
        hardware,
        "_detect_gpus_nvidia_smi",
        lambda: [hardware.GpuInfo(index=0, name="stub-gpu", total_mib=4096, free_mib=3000)],
    )
    monkeypatch.setattr(hardware, "_detect_gpus_paddle", lambda: None)

    profile = hardware.detect()

    assert profile.source == "nvidia-smi"
    assert len(profile.gpus) == 1
    assert profile.gpus[0].name == "stub-gpu"


def test_nvidia_smi_not_on_path_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda _cmd: None)

    assert hardware._detect_gpus_nvidia_smi() is None


def test_nvidia_smi_parses_csv_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware.shutil, "which", lambda _cmd: "/usr/bin/nvidia-smi")

    class _FakeResult:
        stdout = "0, NVIDIA A100, 40960, 39000\n1, NVIDIA A100, 40960, 38000\n"

    monkeypatch.setattr(hardware.subprocess, "run", lambda *a, **kw: _FakeResult())

    gpus = hardware._detect_gpus_nvidia_smi()

    assert gpus is not None
    assert len(gpus) == 2
    assert gpus[0] == hardware.GpuInfo(index=0, name="NVIDIA A100", total_mib=40960, free_mib=39000)


def test_paddle_fallback_used_last_and_assumes_conservative_vram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware, "_detect_gpus_pynvml", lambda: None)
    monkeypatch.setattr(hardware, "_detect_gpus_nvidia_smi", lambda: None)

    class _FakeCuda:
        @staticmethod
        def device_count() -> int:
            return 2

    class _FakeDevice:
        cuda = _FakeCuda()

    class _FakePaddle:
        device = _FakeDevice()

    def _fake_detect_gpus_paddle() -> list[hardware.GpuInfo] | None:
        try:
            # Real, found-not-assumed bug this replaced: a raw
            # `sys.modules["paddle"] = _FakePaddle()` here (not
            # `monkeypatch.setitem`, so never restored) permanently
            # corrupted the real `paddle` module for the rest of the test
            # session — later tests in the same run that actually construct
            # a real PPStructureV3 pipeline failed with `ValueError:
            # paddle.__spec__ is not set` inside paddlex's own
            # `is_dep_available()`. Not needed anyway: this fake never reads
            # `paddle` back out of `sys.modules`, it just calls the fake
            # object directly.
            count = _FakePaddle().device.cuda.device_count()
            return [
                hardware.GpuInfo(index=i, name="unknown", total_mib=4096, free_mib=4096)
                for i in range(count)
            ]
        except Exception:
            return None

    monkeypatch.setattr(hardware, "_detect_gpus_paddle", _fake_detect_gpus_paddle)

    profile = hardware.detect()

    assert profile.source == "paddle"
    assert len(profile.gpus) == 2
    assert all(g.total_mib == 4096 for g in profile.gpus)


def test_gpu_detector_raising_unexpectedly_does_not_crash_detect(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> None:
        raise RuntimeError("simulated crash inside a detector")

    monkeypatch.setattr(hardware, "_detect_gpus_pynvml", _boom)
    monkeypatch.setattr(hardware, "_detect_gpus_nvidia_smi", lambda: None)
    monkeypatch.setattr(hardware, "_detect_gpus_paddle", lambda: None)

    profile = hardware.detect()

    assert profile.gpus == []


def test_cpu_count_never_raises_and_is_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate no /sys/fs/cgroup/cpu.max (not in a container / cgroup v1) by
    # making `open` raise for that specific path only.
    real_open = builtins.open

    def _fake_open(path: object, *args: object, **kwargs: object) -> object:
        if isinstance(path, str) and "cgroup" in path:
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _fake_open)

    count = hardware._detect_cpu_count()

    assert count >= 1


def test_cpu_count_respects_cgroup_quota(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    # Cleanly injectable: _detect_cpu_count() opens a hardcoded path, so we
    # monkeypatch `open` to redirect just that path to a tmp file simulating
    # cgroup v2's cpu.max content ("<quota> <period>").
    import pathlib

    cgroup_file = pathlib.Path(str(tmp_path)) / "cpu.max"
    cgroup_file.write_text("150000 100000\n")  # 1.5 cores -> ceil = 2

    real_open = builtins.open

    def _fake_open(path: object, *args: object, **kwargs: object) -> object:
        if path == "/sys/fs/cgroup/cpu.max":
            return real_open(cgroup_file, *args, **kwargs)  # type: ignore[arg-type]
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _fake_open)
    # Make os.cpu_count()/affinity large so the cgroup quota is the binding
    # constraint being tested.
    monkeypatch.setattr(hardware.os, "cpu_count", lambda: 32)
    if hasattr(hardware.os, "sched_getaffinity"):
        monkeypatch.setattr(hardware.os, "sched_getaffinity", lambda _pid: set(range(32)))

    count = hardware._detect_cpu_count()

    assert count == 2


def test_ram_detection_defaults_when_meminfo_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = builtins.open

    def _fake_open(path: object, *args: object, **kwargs: object) -> object:
        if path == "/proc/meminfo":
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _fake_open)

    ram = hardware._detect_ram_mib()

    assert ram == hardware._DEFAULT_RAM_MIB


def test_avx512_defaults_false_when_cpuinfo_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = builtins.open

    def _fake_open(path: object, *args: object, **kwargs: object) -> object:
        if path == "/proc/cpuinfo":
            raise FileNotFoundError(path)
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _fake_open)

    assert hardware._detect_avx512() is False


def test_detect_returns_hardware_profile_type() -> None:
    # Full end-to-end call against the real host (whatever it may or may
    # not have) -- the contract under test is simply "never raises, always
    # returns a HardwareProfile".
    profile = hardware.detect()
    assert isinstance(profile, hardware.HardwareProfile)
    assert isinstance(profile.gpus, list)
    assert profile.cpu_count >= 1
    assert profile.total_ram_mib >= 1
