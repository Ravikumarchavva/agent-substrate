"""Hardware detection for the document-intelligence service's autoconfig.

``detect()`` never raises — every detection step is a soft-fail fallback to
the next, ending in a usable (if empty/conservative) :class:`HardwareProfile`
when every method fails. This is deliberate: autoconfig must always be able
to resolve *some* runtime (see ``autoconfig.py``), even on a host with no
GPU tooling installed at all.

GPU detection chain, each step a soft-fail fallback to the next:
1. ``CUDA_VISIBLE_DEVICES`` operator override (empty string -> zero GPUs).
2. ``pynvml`` (PyPI package ``nvidia-ml-py``) -- count + real per-device VRAM.
3. ``nvidia-smi`` subprocess, CSV output -- count + real per-device VRAM.
4. ``paddle.device.cuda.device_count()`` -- count only, VRAM assumed 4096 MiB.
5. Total failure -- ``gpus=[]``.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from substrate.logger import setup_logging

logger = setup_logging("substrate.document_intelligence.hardware")

# Conservative assumed per-card VRAM (MiB) when only a GPU *count* is
# available (the paddle fallback gives no memory info at all).
_ASSUMED_VRAM_MIB = 4096

# Default RAM (MiB) assumed when /proc/meminfo can't be read.
_DEFAULT_RAM_MIB = 2048


@dataclass(frozen=True, slots=True)
class GpuInfo:
    """One detected GPU."""

    index: int
    name: str
    total_mib: int
    free_mib: int


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Detected host hardware, used by ``autoconfig.resolve_runtime``."""

    gpus: list[GpuInfo] = field(default_factory=list)
    cpu_count: int = 1
    total_ram_mib: int = _DEFAULT_RAM_MIB
    has_avx512: bool = False
    source: str = "unknown"


def _parse_cuda_visible_devices() -> list[int] | None:
    """Return the operator-requested GPU index allowlist, or ``None`` if the
    env var isn't set at all. An empty string means "zero GPUs" per the
    NVIDIA container runtime convention."""
    raw = os.environ.get("CUDA_VISIBLE_DEVICES")
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return []
    indices: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            indices.append(int(part))
        except ValueError:
            # UUID-style entries (`GPU-xxxx`) aren't index-filterable here;
            # give up on filtering and let the detection method return
            # whatever it finds unfiltered rather than crash.
            return None
    return indices


def _detect_gpus_pynvml() -> list[GpuInfo] | None:
    try:
        import pynvml  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("pynvml not installed, skipping")
        return None

    try:
        pynvml.nvmlInit()
        try:
            count = pynvml.nvmlDeviceGetCount()
            gpus: list[GpuInfo] = []
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpus.append(
                    GpuInfo(
                        index=i,
                        name=name,
                        total_mib=int(mem.total // (1024 * 1024)),
                        free_mib=int(mem.free // (1024 * 1024)),
                    )
                )
            return gpus
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        logger.debug("pynvml GPU detection failed", exc_info=True)
        return None


def _detect_gpus_nvidia_smi() -> list[GpuInfo] | None:
    if shutil.which("nvidia-smi") is None:
        logger.debug("nvidia-smi not on PATH, skipping")
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        logger.debug("nvidia-smi invocation failed", exc_info=True)
        return None

    try:
        gpus: list[GpuInfo] = []
        reader = csv.reader(io.StringIO(result.stdout))
        for row in reader:
            if not row:
                continue
            row = [c.strip() for c in row]
            if len(row) < 4:
                continue
            gpus.append(
                GpuInfo(
                    index=int(row[0]),
                    name=row[1],
                    total_mib=int(row[2]),
                    free_mib=int(row[3]),
                )
            )
        return gpus
    except Exception:
        logger.debug("nvidia-smi output parse failed", exc_info=True)
        return None


def _detect_gpus_paddle() -> list[GpuInfo] | None:
    try:
        import paddle  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("paddle not installed, skipping")
        return None

    try:
        count = paddle.device.cuda.device_count()
        return [
            GpuInfo(index=i, name="unknown", total_mib=_ASSUMED_VRAM_MIB, free_mib=_ASSUMED_VRAM_MIB)
            for i in range(count)
        ]
    except Exception:
        logger.debug("paddle GPU count detection failed", exc_info=True)
        return None


def _filter_by_allowlist(gpus: list[GpuInfo], allowlist: list[int] | None) -> list[GpuInfo]:
    if allowlist is None:
        return gpus
    return [g for g in gpus if g.index in allowlist]


def _detect_gpus() -> tuple[list[GpuInfo], str]:
    allowlist = _parse_cuda_visible_devices()
    if allowlist == []:
        # CUDA_VISIBLE_DEVICES="" -- operator explicitly requested zero GPUs.
        return [], "cuda_visible_devices_override"

    for detector, source in (
        (_detect_gpus_pynvml, "pynvml"),
        (_detect_gpus_nvidia_smi, "nvidia-smi"),
        (_detect_gpus_paddle, "paddle"),
    ):
        try:
            gpus = detector()
        except Exception:
            logger.debug("GPU detector %s raised unexpectedly", source, exc_info=True)
            gpus = None
        if gpus is not None:
            return _filter_by_allowlist(gpus, allowlist), source

    return [], "none"


def _detect_cpu_count() -> int:
    counts = [os.cpu_count() or 1]

    if hasattr(os, "sched_getaffinity"):
        try:
            counts.append(len(os.sched_getaffinity(0)))
        except Exception:
            logger.debug("sched_getaffinity failed", exc_info=True)

    try:
        with open("/sys/fs/cgroup/cpu.max") as f:
            content = f.read().strip()
        quota_str, period_str = content.split()
        if quota_str != "max":
            quota = int(quota_str)
            period = int(period_str)
            if period > 0:
                cgroup_cpus = -(-quota // period)  # ceil division
                counts.append(max(1, cgroup_cpus))
    except Exception:
        logger.debug("cgroup cpu.max read failed", exc_info=True)

    return max(1, min(counts))


def _detect_ram_mib() -> int:
    total_mib = _DEFAULT_RAM_MIB
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    total_mib = kb // 1024
                    break
    except Exception:
        logger.debug("/proc/meminfo read failed", exc_info=True)
        return _DEFAULT_RAM_MIB

    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            content = f.read().strip()
        if content != "max":
            cgroup_mib = int(content) // (1024 * 1024)
            total_mib = min(total_mib, cgroup_mib)
    except Exception:
        logger.debug("cgroup memory.max read failed", exc_info=True)

    return total_mib


def _detect_avx512() -> bool:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("flags") and "avx512f" in line:
                    return True
        return False
    except Exception:
        logger.debug("/proc/cpuinfo read failed", exc_info=True)
        return False


def detect() -> HardwareProfile:
    """Detect this host's hardware profile for autoconfig. Never raises."""
    try:
        gpus, source = _detect_gpus()
    except Exception:
        logger.debug("GPU detection chain failed unexpectedly", exc_info=True)
        gpus, source = [], "none"

    try:
        cpu_count = _detect_cpu_count()
    except Exception:
        logger.debug("CPU count detection failed unexpectedly", exc_info=True)
        cpu_count = 1

    try:
        total_ram_mib = _detect_ram_mib()
    except Exception:
        logger.debug("RAM detection failed unexpectedly", exc_info=True)
        total_ram_mib = _DEFAULT_RAM_MIB

    try:
        has_avx512 = _detect_avx512()
    except Exception:
        logger.debug("AVX-512 detection failed unexpectedly", exc_info=True)
        has_avx512 = False

    return HardwareProfile(
        gpus=gpus,
        cpu_count=cpu_count,
        total_ram_mib=total_ram_mib,
        has_avx512=has_avx512,
        source=source,
    )


__all__ = ["GpuInfo", "HardwareProfile", "detect"]
