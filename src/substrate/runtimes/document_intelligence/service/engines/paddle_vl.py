"""PaddleOCR-VL-1.6 — modes ``vl_cpu``/``vl_gpu``.

Genuinely more accurate than the classic engine on dense financial tables
(verified this session via a direct comparison on a real SEC 10-K balance
sheet — PPStructureV3 row-shifted real numbers, PaddleOCR-VL read every
cell correctly), at the cost of needing a real ``llama-server`` (or a
remote sglang/vLLM deployment) to serve the vision-language model.

Layout detection inside this pipeline is STILL classic Paddle models — only
the text/OCR recognition step is handed off to the VL model over HTTP (via
whatever :class:`~...pool_types.PoolWorker` the pool hands this engine).
That's why this module still needs ``_disable_mkldnn()``/
``_parallelize_crop_image_regions()`` from ``paddle_classic.py``, and why
result parsing reuses that module's private helpers — the ``predict()``
result shape (``parsing_res_list``/``layout_det_res``/``.markdown``) is the
same PaddleX layout-parsing pipeline family, verified this session against
the real installed package on both the cover page and the balance-sheet
page of a real 267-page filing.

Built via the raw ``paddlex.inference.pipelines.create_pipeline`` +
``load_pipeline_config`` API, NOT the ``paddleocr.PaddleOCRVL`` convenience
wrapper — that wrapper doesn't expose the fine-grained ``batch_size``
control this session found necessary to fit both the VL model and layout
detection on a 4GB GPU together (verified: default ``batch_size=64``
top-level / ``8`` per-submodule caused real, repeated VRAM exhaustion;
``4`` throughout is the smallest verified-working config).
"""

from __future__ import annotations

import asyncio

from substrate.logger import setup_logging
from substrate.runtimes.document_intelligence.service.engines.paddle_classic import (
    _disable_mkldnn,
    _finalize_markdown_for_pipeline,
    _parallelize_crop_image_regions,
    _pages_from_results_for_pipeline,
)
from substrate.runtimes.document_intelligence.service.pool_types import (
    InferencePool,
    PoolWorker,
)
from substrate.runtimes.document_intelligence.service.types import ExtractionResult

logger = setup_logging()

_PATCHES_APPLIED = False


def _apply_paddle_patches_once() -> None:
    """``_disable_mkldnn()``/``_parallelize_crop_image_regions()`` are each
    individually safe to call more than once (they check their own existing
    state — see their docstrings in ``paddle_classic.py``), but there's no
    reason to pay the (tiny) redundant-call cost more than once per process
    either."""
    global _PATCHES_APPLIED
    if _PATCHES_APPLIED:
        return
    _disable_mkldnn()
    _parallelize_crop_image_regions()
    _PATCHES_APPLIED = True


def _build_pipeline_config(
    *,
    server_url: str,
    layout_batch_size: int,
    doc_preproc_batch_size: int,
):
    """The real, verified-working PaddleOCR-VL-1.6 config shape — read
    directly from ``load_pipeline_config`` and printed this session, not
    guessed. Every ``batch_size`` field defaults far higher (64 top-level,
    8 per-submodule) than what actually fits alongside a resident VL model
    on a 4GB GPU; ``layout_batch_size``/``doc_preproc_batch_size`` (both
    default 4, the verified value) override every one of them."""
    from paddlex.inference.pipelines import load_pipeline_config

    cfg = load_pipeline_config("PaddleOCR-VL-1.6")
    cfg["batch_size"] = layout_batch_size
    cfg["SubModules"]["LayoutDetection"]["batch_size"] = layout_batch_size
    cfg["SubPipelines"]["DocPreprocessor"]["batch_size"] = doc_preproc_batch_size
    cfg["SubPipelines"]["DocPreprocessor"]["SubModules"]["DocOrientationClassify"][
        "batch_size"
    ] = doc_preproc_batch_size
    cfg["SubModules"]["VLRecognition"]["genai_config"] = {
        "backend": "llama-cpp-server",
        "server_url": server_url,
        "max_concurrency": 1,
    }
    return cfg


class PaddleVLEngine:
    """PaddleOCR-VL-1.6, dispatched through an :class:`InferencePool` so the
    same engine class serves both ``vl_cpu``/``vl_gpu`` (a local
    ``LocalLlamaServerPool``) and a future remote-URL deployment (a
    ``RemoteInferencePool``) with zero code change — callers of the pool
    can't tell which they have, and this class never branches on it."""

    name = "paddleocr-vl"

    def __init__(
        self,
        pool: InferencePool,
        *,
        device_mode: str,
        ctx_size: int = 6144,
        max_new_tokens: int = 3000,
        layout_batch_size: int = 4,
        doc_preproc_batch_size: int = 4,
    ) -> None:
        self._pool = pool
        self._device_mode = device_mode
        self._ctx_size = ctx_size
        self._max_new_tokens = max_new_tokens
        self._layout_batch_size = layout_batch_size
        self._doc_preproc_batch_size = doc_preproc_batch_size
        # One real PaddleOCR-VL pipeline object PER WORKER, built lazily on
        # first use and reused thereafter — construction has real overhead
        # (a few seconds, verified this session), not something to pay
        # per-request.
        self._pipelines: dict[int, object] = {}

    def supported_formats(self) -> set[str]:
        return {"application/pdf", "image/png", "image/jpeg"}

    def accepts(self, filename: str, content_type: str) -> bool:
        return content_type in self.supported_formats()

    def warmup(self) -> None:
        """Best-effort only — building a real pipeline/warming a worker
        needs the pool to already be started (async), which this sync,
        best-effort hook can't await. Real warmup for this engine happens
        naturally on the first real request; this just logs readiness."""
        try:
            if not self._pool.ready:
                logger.info(
                    "PaddleVLEngine.warmup: pool not yet ready (%d workers)",
                    self._pool.worker_count,
                )
        except Exception:
            pass  # best-effort — never block startup

    async def aclose(self) -> None:
        await self._pool.aclose()

    def _pipeline_for(self, worker: PoolWorker):
        pipeline = self._pipelines.get(worker.index)
        if pipeline is not None:
            return pipeline

        _apply_paddle_patches_once()
        from paddlex.inference.pipelines import create_pipeline

        cfg = _build_pipeline_config(
            server_url=worker.endpoint.base_url or "",
            layout_batch_size=self._layout_batch_size,
            doc_preproc_batch_size=self._doc_preproc_batch_size,
        )
        pipeline = create_pipeline(config=cfg, device=worker.layout_device)
        self._pipelines[worker.index] = pipeline
        return pipeline

    async def _acquire_or_raise(self) -> PoolWorker:
        if not self._pool.ready or self._pool.worker_count == 0:
            raise RuntimeError(
                f"PaddleVLEngine ({self.name}, device_mode={self._device_mode}): "
                "no healthy inference workers available — the pool never "
                "became ready or every worker has died. This is a real "
                "service-level problem the caller must handle via "
                "degradation, not something to paper over with an empty "
                "result."
            )
        return await self._pool.acquire()

    def _predict_sync(self, pipeline, path: str) -> ExtractionResult:
        results = list(pipeline.predict(path, max_new_tokens=self._max_new_tokens))
        pages, markdown_pages = _pages_from_results_for_pipeline(results)
        markdown = _finalize_markdown_for_pipeline(pipeline, pages, markdown_pages)
        return ExtractionResult(pages=pages, markdown=markdown, engine=self.name)

    async def aextract(self, data: bytes, filename: str) -> ExtractionResult:
        import tempfile
        from pathlib import Path

        worker = await self._acquire_or_raise()
        try:
            pipeline = self._pipeline_for(worker)
            suffix = Path(filename).suffix or ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
                tmp.write(data)
                tmp.flush()
                return await asyncio.to_thread(self._predict_sync, pipeline, tmp.name)
        finally:
            self._pool.release(worker)

    async def aextract_batch(
        self, items: list[tuple[bytes, str]]
    ) -> list[ExtractionResult]:
        """No special multi-file predict()-batching here (unlike
        ``PaddleClassicEngine.extract_batch`` — that optimization is
        specific to how PPStructureV3's local batch sampler groups OCR
        inference across files; the VL model's real cost is the network
        round trip to the pool's worker, which bounded concurrency across
        items already parallelizes)."""
        if not items:
            return []
        semaphore = asyncio.Semaphore(max(1, self._pool.worker_count))

        async def _one(data: bytes, filename: str) -> ExtractionResult:
            async with semaphore:
                return await self.aextract(data, filename)

        return await asyncio.gather(*(_one(d, f) for d, f in items))


__all__ = ["PaddleVLEngine"]
