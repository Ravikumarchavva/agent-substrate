"""Pure resolver: ``(ServiceConfig, HardwareProfile) -> ResolvedRuntime``.

No I/O, no subprocess calls, no importing paddle/paddleocr -- every rule is
table-testable against synthetic hardware with zero GPU dependency.

Verified constants (real measurements, not extrapolated) are documented
inline next to the rule that uses them; see the phase-1 plan for the
benchmark this session ran them against. Values only ever marked
``"extrapolated, unbenchmarked"`` in ``ResolvedRuntime.reasons`` were never
actually tested -- treat them with correspondingly less confidence.

``cfg`` is accepted as a generic object (not a concrete ``ServiceConfig``
type) and read defensively via ``getattr`` so this module has zero import
dependency on the (still-in-progress, built in parallel) config module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from substrate.runtimes.document_intelligence.service.hardware import HardwareProfile

# GPU eligibility threshold (MiB). NOT 3500 -- Paddle's own allocator showed
# real run-to-run variance of a few hundred MB in this session's testing;
# the per-worker budget of ~1.3GB llama + ~2.2GB layout stack needs real
# margin against a 4GB card.
_GPU_ELIGIBLE_MIB = 3800

# Above this per-GPU VRAM, scale batch/slot sizing up (see resolve_runtime).
_GPU_SCALE_UP_MIB = 16384

# Verified constants (real measurements against this session's benchmark).
_LLAMA_CTX = 6144
_LLAMA_SLOTS_DEFAULT = 1
_LLAMA_SLOTS_SCALED = 2
_MAX_NEW_TOKENS_DEFAULT = 3000
_LAYOUT_BATCH_SIZE_DEFAULT = 4
_LAYOUT_BATCH_SIZE_SCALED = 8
_DOC_PREPROC_BATCH_SIZE = 4
_QUANT_DEFAULT = "q8_0"
_LLAMA_NGL_GPU = 99
_LLAMA_NGL_CPU = 0
_VL_CPU_MIN_RAM_MIB = 4096


@dataclass(frozen=True, slots=True)
class ResolvedRuntime:
    """The fully-resolved runtime configuration autoconfig produced."""

    mode: str  # "raw_text" | "vl_cpu" | "vl_gpu" | "ocr_classic"
    worker_count: int
    layout_device_for_worker: list[str] = field(default_factory=list)
    llama_ngl: int = _LLAMA_NGL_CPU
    llama_threads: int | None = None
    llama_ctx: int = _LLAMA_CTX
    llama_slots: int = _LLAMA_SLOTS_DEFAULT
    quant: str = _QUANT_DEFAULT
    layout_batch_size: int = _LAYOUT_BATCH_SIZE_DEFAULT
    doc_preproc_batch_size: int = _DOC_PREPROC_BATCH_SIZE
    max_new_tokens: int = _MAX_NEW_TOKENS_DEFAULT
    max_pages_per_call: int | None = None
    degraded_from: str | None = None
    reasons: list[str] = field(default_factory=list)


def _eligible_gpus(hw: HardwareProfile) -> list:
    return [g for g in hw.gpus if g.total_mib >= _GPU_ELIGIBLE_MIB]


def _raw_text(*, degraded_from: str | None, reasons: list[str], cfg: Any) -> ResolvedRuntime:
    return ResolvedRuntime(
        mode="raw_text",
        worker_count=0,
        layout_device_for_worker=[],
        max_pages_per_call=getattr(cfg, "max_pages_per_call", None),
        degraded_from=degraded_from,
        reasons=reasons,
    )


def _vl_cpu(*, hw: HardwareProfile, cfg: Any, degraded_from: str | None, reasons: list[str]) -> ResolvedRuntime:
    reasons = list(reasons)
    reasons.append(
        "llama_threads = max(1, cpu_count-1) is an unbenchmarked heuristic, unlike the "
        "GPU numbers, which are verified."
    )
    llama_threads = max(1, hw.cpu_count - 1)
    ctx = getattr(cfg, "vl_ctx_size", None)
    slots = getattr(cfg, "vl_slots", None)
    threads = getattr(cfg, "vl_threads", None)
    max_new_tokens = getattr(cfg, "vl_max_new_tokens", None)
    quant = getattr(cfg, "vl_quant", None) or _QUANT_DEFAULT
    return ResolvedRuntime(
        mode="vl_cpu",
        worker_count=1,
        layout_device_for_worker=["cpu"],
        llama_ngl=_LLAMA_NGL_CPU,
        llama_threads=threads if threads is not None else llama_threads,
        llama_ctx=ctx if ctx is not None else _LLAMA_CTX,
        llama_slots=slots if slots is not None else _LLAMA_SLOTS_DEFAULT,
        quant=quant,
        layout_batch_size=getattr(cfg, "layout_batch_size", None) or _LAYOUT_BATCH_SIZE_DEFAULT,
        doc_preproc_batch_size=_DOC_PREPROC_BATCH_SIZE,
        max_new_tokens=max_new_tokens if max_new_tokens is not None else _MAX_NEW_TOKENS_DEFAULT,
        max_pages_per_call=getattr(cfg, "max_pages_per_call", None),
        degraded_from=degraded_from,
        reasons=reasons,
    )


def _vl_gpu(*, hw: HardwareProfile, cfg: Any, reasons: list[str]) -> ResolvedRuntime:
    eligible = _eligible_gpus(hw)
    reasons = list(reasons)

    layout_batch_size = getattr(cfg, "layout_batch_size", None)
    llama_slots = getattr(cfg, "vl_slots", None)

    scaled = bool(eligible) and max(g.total_mib for g in eligible) >= _GPU_SCALE_UP_MIB
    if scaled:
        reasons.append(
            "extrapolated, unbenchmarked: >=16GB per-GPU scaling "
            "(layout_batch_size=8, llama_slots=2) was never actually tested this session, "
            "only the 4GB-card numbers were."
        )

    ctx = getattr(cfg, "vl_ctx_size", None)
    max_new_tokens = getattr(cfg, "vl_max_new_tokens", None)
    quant = getattr(cfg, "vl_quant", None) or _QUANT_DEFAULT

    return ResolvedRuntime(
        mode="vl_gpu",
        worker_count=len(eligible),
        layout_device_for_worker=[f"gpu:{g.index}" for g in eligible],
        llama_ngl=_LLAMA_NGL_GPU,
        llama_threads=None,
        llama_ctx=ctx if ctx is not None else _LLAMA_CTX,
        llama_slots=(
            llama_slots
            if llama_slots is not None
            else (_LLAMA_SLOTS_SCALED if scaled else _LLAMA_SLOTS_DEFAULT)
        ),
        quant=quant,
        layout_batch_size=(
            layout_batch_size
            if layout_batch_size is not None
            else (_LAYOUT_BATCH_SIZE_SCALED if scaled else _LAYOUT_BATCH_SIZE_DEFAULT)
        ),
        doc_preproc_batch_size=_DOC_PREPROC_BATCH_SIZE,
        max_new_tokens=max_new_tokens if max_new_tokens is not None else _MAX_NEW_TOKENS_DEFAULT,
        max_pages_per_call=getattr(cfg, "max_pages_per_call", None),
        degraded_from=None,
        reasons=reasons,
    )


def _ocr_classic(*, hw: HardwareProfile, cfg: Any) -> ResolvedRuntime:
    device = ["gpu:0"] if hw.gpus else ["cpu"]
    return ResolvedRuntime(
        mode="ocr_classic",
        worker_count=1,
        layout_device_for_worker=device,
        max_pages_per_call=getattr(cfg, "max_pages_per_call", None),
        degraded_from=None,
        reasons=[],
    )


def resolve_runtime(cfg: Any, hw: HardwareProfile) -> ResolvedRuntime:
    """Resolve the concrete runtime to build, given requested ``cfg.mode``
    and detected hardware ``hw``. Pure function -- no I/O, no subprocess,
    no paddle import."""
    mode = getattr(cfg, "mode", "auto") or "auto"
    eligible = _eligible_gpus(hw)

    if mode == "raw_text":
        return _raw_text(degraded_from=None, reasons=[], cfg=cfg)

    if mode == "ocr_classic":
        return _ocr_classic(hw=hw, cfg=cfg)

    if mode == "vl_gpu":
        if eligible:
            return _vl_gpu(hw=hw, cfg=cfg, reasons=[])
        # Degrade: no eligible GPU.
        reason = (
            f"requested vl_gpu but no GPU with >={_GPU_ELIGIBLE_MIB} MiB free "
            "-- degraded to vl_cpu"
        )
        if hw.total_ram_mib >= _VL_CPU_MIN_RAM_MIB:
            return _vl_cpu(hw=hw, cfg=cfg, degraded_from="vl_gpu", reasons=[reason])
        reason2 = (
            f"requested vl_gpu but no GPU with >={_GPU_ELIGIBLE_MIB} MiB free and "
            f"insufficient RAM (<{_VL_CPU_MIN_RAM_MIB} MiB) for vl_cpu -- degraded to raw_text"
        )
        return _raw_text(degraded_from="vl_gpu", reasons=[reason, reason2], cfg=cfg)

    if mode == "vl_cpu":
        if hw.total_ram_mib >= _VL_CPU_MIN_RAM_MIB:
            return _vl_cpu(hw=hw, cfg=cfg, degraded_from=None, reasons=[])
        reason = (
            f"requested vl_cpu but insufficient RAM (<{_VL_CPU_MIN_RAM_MIB} MiB) "
            "-- degraded to raw_text"
        )
        return _raw_text(degraded_from="vl_cpu", reasons=[reason], cfg=cfg)

    # mode == "auto" (or anything unrecognized falls back to auto behavior)
    if eligible:
        return _vl_gpu(hw=hw, cfg=cfg, reasons=[])
    if hw.total_ram_mib >= _VL_CPU_MIN_RAM_MIB:
        return _vl_cpu(hw=hw, cfg=cfg, degraded_from=None, reasons=[])
    return _raw_text(degraded_from=None, reasons=[], cfg=cfg)


def resolve_child_setting(
    explicit_value: Any,
    inherited_value: Any,
    *,
    local_selector_present: bool = False,
) -> Any:
    """Cascading config-override precedence, mirroring
    ``paddlex.inference.pipelines.base.BasePipeline._resolve_child_engine``
    (verified read from the installed ``paddlex`` package this session, not
    guessed).

    Precedence:
    1. A same-level explicit value always wins.
    2. Else, if the child declares its own local selector (e.g. a specific
       device override, or a remote backend URL of its own,
       ``local_selector_present=True``), that beats blindly inheriting the
       parent's value -- returns ``None`` so the caller auto-resolves
       locally instead of inheriting.
    3. Else the parent's value is inherited untouched.

    Not yet wired into every field of ``resolve_runtime`` above -- today's
    config is flat and single-level, so threading this through every field
    would be over-engineering. It exists now so a later phase (nested
    per-submodule config, if that ever happens) has the precedence rule
    ready as a named, reusable function rather than ad hoc per-field
    ``None``-means-inherit checks scattered through the resolver.
    """
    if explicit_value is not None:
        return explicit_value
    if local_selector_present:
        return None
    return inherited_value


__all__ = ["ResolvedRuntime", "resolve_runtime", "resolve_child_setting"]
