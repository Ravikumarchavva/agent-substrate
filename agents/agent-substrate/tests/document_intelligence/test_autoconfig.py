"""Table-driven unit tests for autoconfig.resolve_runtime and
resolve_child_setting.

resolve_runtime is a pure function -- these tests use only synthetic
HardwareProfile instances and a SimpleNamespace stand-in for the
(not-yet-built) ServiceConfig, matching the decoupling autoconfig.py itself
implements via getattr().
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from substrate.runtimes.document_intelligence.service.autoconfig import (
    resolve_child_setting,
    resolve_runtime,
)
from substrate.runtimes.document_intelligence.service.hardware import GpuInfo, HardwareProfile


def _cfg(**overrides: object) -> SimpleNamespace:
    defaults = dict(
        mode="auto",
        vl_quant="q8_0",
        vl_workers=None,
        vl_ctx_size=None,
        vl_slots=None,
        vl_threads=None,
        vl_max_new_tokens=3000,
        layout_device=None,
        layout_batch_size=None,
        max_pages_per_call=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _hw(gpus: list[GpuInfo], *, cpu_count: int = 8, total_ram_mib: int = 16384) -> HardwareProfile:
    return HardwareProfile(gpus=gpus, cpu_count=cpu_count, total_ram_mib=total_ram_mib, has_avx512=False, source="test")


HW_NO_GPU = _hw([])
HW_1X4GB = _hw([GpuInfo(index=0, name="4gb", total_mib=4096, free_mib=4000)])
HW_1X3GB_INELIGIBLE = _hw([GpuInfo(index=0, name="3gb", total_mib=3072, free_mib=3000)])
HW_2X4GB = _hw(
    [
        GpuInfo(index=0, name="4gb-a", total_mib=4096, free_mib=4000),
        GpuInfo(index=1, name="4gb-b", total_mib=4096, free_mib=4000),
    ]
)
HW_1X24GB = _hw([GpuInfo(index=0, name="24gb", total_mib=24576, free_mib=24000)])
HW_6X24GB = _hw([GpuInfo(index=i, name="24gb", total_mib=24576, free_mib=24000) for i in range(6)])


# ── mode="auto" ──────────────────────────────────────────────────────────


def test_auto_no_gpu_enough_ram_resolves_vl_cpu() -> None:
    result = resolve_runtime(_cfg(mode="auto"), HW_NO_GPU)
    assert result.mode == "vl_cpu"
    assert result.worker_count == 1
    assert result.degraded_from is None


def test_auto_no_gpu_insufficient_ram_resolves_raw_text() -> None:
    hw = _hw([], total_ram_mib=2048)
    result = resolve_runtime(_cfg(mode="auto"), hw)
    assert result.mode == "raw_text"
    assert result.worker_count == 0
    assert result.degraded_from is None


def test_auto_below_threshold_gpu_is_not_eligible_falls_back_to_vl_cpu() -> None:
    # 3GB card is below the 3800 MiB threshold -- must NOT be treated as eligible.
    result = resolve_runtime(_cfg(mode="auto"), HW_1X3GB_INELIGIBLE)
    assert result.mode == "vl_cpu"
    assert result.worker_count == 1


def test_auto_1x4gb_resolves_vl_gpu_with_verified_constants() -> None:
    result = resolve_runtime(_cfg(mode="auto"), HW_1X4GB)

    assert result.mode == "vl_gpu"
    assert result.degraded_from is None
    assert result.worker_count == 1
    assert result.layout_device_for_worker == ["gpu:0"]
    # Verified constants -- the regression guard on this session's real
    # measurements. Do not change these without new benchmark evidence.
    assert result.llama_ctx == 6144
    assert result.llama_slots == 1
    assert result.max_new_tokens == 3000
    assert result.layout_batch_size == 4
    assert result.doc_preproc_batch_size == 4
    assert result.quant == "q8_0"
    assert result.llama_ngl == 99
    assert result.llama_threads is None
    assert "extrapolated, unbenchmarked" not in " ".join(result.reasons)


def test_auto_2x4gb_resolves_vl_gpu_with_two_workers() -> None:
    result = resolve_runtime(_cfg(mode="auto"), HW_2X4GB)

    assert result.mode == "vl_gpu"
    assert result.worker_count == 2
    assert result.layout_device_for_worker == ["gpu:0", "gpu:1"]
    # Still under the 16GB scale-up threshold.
    assert result.layout_batch_size == 4
    assert result.llama_slots == 1


def test_auto_1x24gb_scales_batch_and_slots() -> None:
    result = resolve_runtime(_cfg(mode="auto"), HW_1X24GB)

    assert result.mode == "vl_gpu"
    assert result.worker_count == 1
    assert result.layout_batch_size == 8
    assert result.llama_slots == 2
    assert any("extrapolated, unbenchmarked" in r for r in result.reasons)
    # llama_ctx does NOT auto-scale even on a big card.
    assert result.llama_ctx == 6144


def test_auto_6x24gb_scales_and_has_six_workers() -> None:
    result = resolve_runtime(_cfg(mode="auto"), HW_6X24GB)

    assert result.mode == "vl_gpu"
    assert result.worker_count == 6
    assert result.layout_device_for_worker == [f"gpu:{i}" for i in range(6)]
    assert result.layout_batch_size == 8
    assert result.llama_slots == 2


# ── explicit mode="vl_gpu" ───────────────────────────────────────────────


def test_explicit_vl_gpu_with_eligible_gpu_is_honored() -> None:
    result = resolve_runtime(_cfg(mode="vl_gpu"), HW_1X4GB)
    assert result.mode == "vl_gpu"
    assert result.degraded_from is None


def test_explicit_vl_gpu_no_eligible_gpu_degrades_to_vl_cpu() -> None:
    result = resolve_runtime(_cfg(mode="vl_gpu"), HW_NO_GPU)
    assert result.mode == "vl_cpu"
    assert result.degraded_from == "vl_gpu"
    assert any("vl_gpu" in r and "vl_cpu" in r for r in result.reasons)


def test_explicit_vl_gpu_no_gpu_and_no_ram_degrades_to_raw_text() -> None:
    hw = _hw([], total_ram_mib=2048)
    result = resolve_runtime(_cfg(mode="vl_gpu"), hw)
    assert result.mode == "raw_text"
    assert result.degraded_from == "vl_gpu"
    assert len(result.reasons) >= 1


def test_explicit_vl_gpu_below_threshold_card_degrades() -> None:
    result = resolve_runtime(_cfg(mode="vl_gpu"), HW_1X3GB_INELIGIBLE)
    assert result.mode == "vl_cpu"
    assert result.degraded_from == "vl_gpu"


# ── explicit mode="vl_cpu" ───────────────────────────────────────────────


def test_explicit_vl_cpu_enough_ram_is_honored() -> None:
    result = resolve_runtime(_cfg(mode="vl_cpu"), HW_NO_GPU)
    assert result.mode == "vl_cpu"
    assert result.degraded_from is None
    assert result.llama_threads == max(1, HW_NO_GPU.cpu_count - 1)
    assert result.llama_ngl == 0


def test_explicit_vl_cpu_insufficient_ram_degrades_to_raw_text() -> None:
    hw = _hw([], total_ram_mib=2048)
    result = resolve_runtime(_cfg(mode="vl_cpu"), hw)
    assert result.mode == "raw_text"
    assert result.degraded_from == "vl_cpu"


def test_vl_cpu_llama_threads_reason_present() -> None:
    result = resolve_runtime(_cfg(mode="vl_cpu"), HW_NO_GPU)
    assert any("unbenchmarked heuristic" in r for r in result.reasons)


# ── explicit mode="raw_text" / "ocr_classic" ─────────────────────────────


def test_explicit_raw_text_always_honored_regardless_of_hardware() -> None:
    for hw in (HW_NO_GPU, HW_1X4GB, HW_6X24GB):
        result = resolve_runtime(_cfg(mode="raw_text"), hw)
        assert result.mode == "raw_text"
        assert result.worker_count == 0
        assert result.degraded_from is None


def test_explicit_ocr_classic_prefers_gpu_when_available() -> None:
    result = resolve_runtime(_cfg(mode="ocr_classic"), HW_1X4GB)
    assert result.mode == "ocr_classic"
    assert result.worker_count == 1
    assert result.layout_device_for_worker == ["gpu:0"]


def test_explicit_ocr_classic_falls_back_to_cpu_when_no_gpu() -> None:
    result = resolve_runtime(_cfg(mode="ocr_classic"), HW_NO_GPU)
    assert result.mode == "ocr_classic"
    assert result.layout_device_for_worker == ["cpu"]


# ── cfg overrides are respected ──────────────────────────────────────────


def test_cfg_overrides_win_over_defaults() -> None:
    cfg = _cfg(mode="vl_gpu", vl_ctx_size=8192, vl_slots=4, vl_quant="q4_k_m", vl_max_new_tokens=500)
    result = resolve_runtime(cfg, HW_1X4GB)
    assert result.llama_ctx == 8192
    assert result.llama_slots == 4
    assert result.quant == "q4_k_m"
    assert result.max_new_tokens == 500


def test_max_pages_per_call_passed_through() -> None:
    cfg = _cfg(mode="raw_text", max_pages_per_call=50)
    result = resolve_runtime(cfg, HW_NO_GPU)
    assert result.max_pages_per_call == 50


# ── resolve_child_setting: the PaddleX-style cascading pattern ──────────


def test_resolve_child_setting_explicit_value_wins() -> None:
    assert resolve_child_setting("gpu:1", "gpu:0", local_selector_present=True) == "gpu:1"
    assert resolve_child_setting("gpu:1", "gpu:0", local_selector_present=False) == "gpu:1"


def test_resolve_child_setting_local_selector_beats_inherited() -> None:
    assert resolve_child_setting(None, "gpu:0", local_selector_present=True) is None


def test_resolve_child_setting_inherits_parent_value() -> None:
    assert resolve_child_setting(None, "gpu:0", local_selector_present=False) == "gpu:0"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
