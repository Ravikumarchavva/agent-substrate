"""``build_engine(cfg, resolved) -> ExtractionEngine`` — the integration
point connecting hardware detection (``hardware.py``) -> autoconfig
(``autoconfig.py``) -> a concrete, ready-to-serve engine. The one place
that knows how to turn a :class:`~..autoconfig.ResolvedRuntime` into an
object implementing :class:`~.base.ExtractionEngine`.

Deliberately built last, after every engine/pool/model-provisioning module
it wires together already exists independently — this file has no logic of
its own to get wrong, just construction order.
"""

from __future__ import annotations

from typing import Any

from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.logger import setup_logging
from substrate.runtimes.document_intelligence.service.autoconfig import ResolvedRuntime
from substrate.runtimes.document_intelligence.service.engines.base import ExtractionEngine
from substrate.runtimes.document_intelligence.service.engines.paddle_classic import (
    PaddleClassicEngine,
)
from substrate.runtimes.document_intelligence.service.engines.paddle_vl import PaddleVLEngine
from substrate.runtimes.document_intelligence.service.engines.raw_text import RawTextEngine
from substrate.runtimes.inference_pool.llama_pool import (
    LocalLlamaServerPool,
    RemoteInferencePool,
)
from substrate.runtimes.document_intelligence.service.models import ensure_models

logger = setup_logging("substrate.document_intelligence.factory")

# Verified this session's benchmark: ~1.3GB resident for the llama-server
# child (Q8_0, ctx=6144) + ~2.2GB for the layout-detection stack (still
# classic Paddle models, running on the same GPU) per worker -- the real
# number the whole Phase 1 batch_size/ctx tuning was chasing. Used only as
# an additive pre-flight admission check against hardware.detect()'s real
# free_mib (see vram_ledger.py's own scope-boundary docstring: this stays
# entirely within THIS process); autoconfig.py's own static
# _GPU_ELIGIBLE_MIB threshold remains the primary, field-verified gate.
_VL_WORKER_VRAM_BUDGET_MIB = 3500


async def _admit_gpu_workers(layout_devices: list[str]) -> list[str]:
    """Drop any GPU worker whose real, currently-detected free VRAM can't
    cover this session's verified per-worker budget -- a dynamic check on
    top of autoconfig.py's static eligibility threshold, catching the case
    where real free_mib is lower than that threshold assumed (e.g. another
    process already holding VRAM on the same card the instant this
    container starts). CPU entries pass through untouched -- there's
    nothing to reserve against."""
    gpu_devices = [d for d in layout_devices if d != "cpu"]
    if not gpu_devices:
        return layout_devices

    from substrate.runtimes.document_intelligence.service.hardware import detect
    from substrate.runtimes.inference_pool.vram_ledger import VramLedger

    hw = detect()
    free_by_device = {f"gpu:{g.index}": g.free_mib for g in hw.gpus}
    ledger = VramLedger(free_by_device)

    admitted: list[str] = []
    for device in layout_devices:
        if device == "cpu":
            admitted.append(device)
            continue
        real_free = ledger.available(device)
        lease = await ledger.reserve(device, _VL_WORKER_VRAM_BUDGET_MIB)
        if lease is None:
            logger.warning(
                "PaddleOCR-VL worker on %s skipped: real free VRAM (%d MiB) "
                "does not cover this session's verified per-worker budget "
                "(%d MiB) -- degrading worker_count by one rather than "
                "risking a real CUDA OOM crash.",
                device,
                real_free,
                _VL_WORKER_VRAM_BUDGET_MIB,
            )
            continue
        admitted.append(device)

    return admitted


async def _build_vl_engine(cfg: Any, resolved: ResolvedRuntime) -> PaddleVLEngine:
    remote_base_url = getattr(cfg, "vl_remote_base_url", None)
    layout_devices = resolved.layout_device_for_worker or ["cpu"]
    if not remote_base_url:
        # Local pool only -- a remote endpoint's own VRAM is someone else's
        # process to arbitrate, not this one's.
        layout_devices = await _admit_gpu_workers(layout_devices)
        if not layout_devices:
            logger.error(
                "PaddleOCR-VL: every GPU worker failed VRAM admission -- "
                "0 workers will be started. This mode's extraction "
                "requests will fail until hardware/VRAM pressure changes."
            )

    if remote_base_url:
        # A pre-existing OpenAI-compatible deployment (sglang/vLLM/another
        # llama-server) -- no local subprocess spawned at all. This is the
        # real proof Phase 0's InferenceEndpoint seam holds for this
        # engine, not just chat models.
        logger.info("PaddleOCR-VL: using remote inference endpoint %s", remote_base_url)
        pool = RemoteInferencePool(
            [
                InferenceEndpoint(
                    model="compatible/PaddleOCR-VL-1.6",
                    base_url=remote_base_url,
                    timeout_s=getattr(cfg, "vl_remote_timeout_s", 90.0),
                )
            ],
            layout_devices=layout_devices,
        )
    else:
        model_dir = getattr(cfg, "vl_model_dir", "/models/paddleocr-vl")
        main_gguf, mmproj_gguf = await ensure_models(
            model_dir,
            quant=resolved.quant,
            llama_quantize_bin=getattr(cfg, "llama_quantize_bin", "llama-quantize"),
            hf_repo=getattr(cfg, "vl_hf_repo", "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"),
        )
        pool = LocalLlamaServerPool(
            binary=getattr(cfg, "llama_server_bin", "llama-server"),
            main_gguf=main_gguf,
            mmproj_gguf=mmproj_gguf,
            gpu_devices=layout_devices,
            base_port=getattr(cfg, "vl_base_port", 8090),
            ngl=resolved.llama_ngl,
            slots=resolved.llama_slots,
            ctx_size=resolved.llama_ctx,
            threads=resolved.llama_threads,
            startup_timeout_s=getattr(cfg, "vl_startup_timeout_s", 300.0),
            max_restarts=getattr(cfg, "vl_max_restarts", 5),
        )

    await pool.start()
    if not pool.ready:
        logger.error(
            "PaddleOCR-VL pool never became ready (0 of %d workers healthy) -- "
            "extraction requests against this engine will fail until a "
            "restart succeeds.",
            len(layout_devices),
        )

    return PaddleVLEngine(
        pool,
        device_mode=resolved.mode,
        ctx_size=resolved.llama_ctx,
        max_new_tokens=resolved.max_new_tokens,
        layout_batch_size=resolved.layout_batch_size,
        doc_preproc_batch_size=resolved.doc_preproc_batch_size,
    )


async def build_engine(cfg: Any, resolved: ResolvedRuntime) -> ExtractionEngine:
    """Construct the one engine this pod will serve, per ``resolved.mode``.

    Async because the VL engines may need to provision GGUF models
    (network I/O) and start a subprocess pool (health-gated, also async)
    before they're ready to accept requests -- both must complete during
    startup, not on the first real request.
    """
    if resolved.mode == "raw_text":
        return RawTextEngine()

    if resolved.mode == "ocr_classic":
        # An explicit cfg.device override always wins over the
        # hardware-resolved device -- same "same-level explicit wins"
        # precedence documented in autoconfig.py::resolve_child_setting,
        # applied here since ocr_classic's device isn't routed through
        # that function today.
        device = getattr(cfg, "device", None) or (
            resolved.layout_device_for_worker[0]
            if resolved.layout_device_for_worker
            else "cpu"
        )
        return PaddleClassicEngine(
            ocr_size=getattr(cfg, "ocr_size", "tiny"),
            device=device,
            ocr_batch_size=getattr(cfg, "ocr_batch_size", 16),
            max_pages_per_call=resolved.max_pages_per_call,
        )

    if resolved.mode in ("vl_cpu", "vl_gpu"):
        return await _build_vl_engine(cfg, resolved)

    raise ValueError(f"Unknown resolved runtime mode: {resolved.mode!r}")


__all__ = ["build_engine"]
