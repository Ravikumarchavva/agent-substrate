"""The inference service: the engine, and (in local mode) the two pools of `llama-server` children behind it."""

from __future__ import annotations

import logging
import time

from loamz.data.server.features.inference.engine import EngineError, LlamaEngine
from loamz.data.server.kernel.container import Container
from loamz.data.server.kernel.settings import InferenceSettings
from loamz.data.storage.errors import ContextLengthError, ServiceUnavailableError
from loamz.data.storage.models.llama import LocalLlamaServerPool, VramLedger, detect

logger = logging.getLogger(__name__)


class InferenceService:
    def __init__(self, settings: InferenceSettings) -> None:
        self.settings = settings
        self.engine: LlamaEngine | None = None
        self.embed_pool: LocalLlamaServerPool | None = None
        self.rerank_pool: LocalLlamaServerPool | None = None
        self.started_at = time.monotonic()

    async def start(self) -> None:
        """Remote mode reaches the configured servers; local mode spawns one embed and one rerank child, each on the
        first GPU whose free memory covers its budget once reserved (else on CPU), and keeps one worker of each."""
        cfg = self.settings
        if cfg.mode == "local":
            hardware = detect()
            ledger = VramLedger({f"gpu:{g.index}": g.free_mib for g in hardware.gpus})
            embed_device = await _device(ledger, hardware, cfg.embed_vram_mib, "embed")
            rerank_device = await _device(ledger, hardware, cfg.rerank_vram_mib, "rerank")
            self.embed_pool = LocalLlamaServerPool(
                binary=cfg.llama_server_bin,
                gpu_devices=[embed_device],
                base_port=cfg.embed_base_port,
                slots=cfg.embed_slots,
                ctx_size=cfg.local_ctx_size,
                startup_timeout_s=cfg.local_startup_timeout_s,
                max_restarts=cfg.local_max_restarts,
                model_name="compatible/Qwen3-VL-Embedding-2B",
                extra_args=[
                    "--hf-repo", cfg.embed_hf_repo, "--hf-file", cfg.embed_hf_file,
                    "--embedding", "--pooling", "last",
                ],
            )  # fmt: skip
            self.rerank_pool = LocalLlamaServerPool(
                binary=cfg.llama_server_bin,
                gpu_devices=[rerank_device],
                base_port=cfg.rerank_base_port,
                slots=cfg.rerank_slots,
                ctx_size=cfg.local_ctx_size,
                startup_timeout_s=cfg.local_startup_timeout_s,
                max_restarts=cfg.local_max_restarts,
                model_name="compatible/Qwen3-VL-Reranker-2B",
                extra_args=[
                    "--hf-repo", cfg.rerank_hf_repo, "--hf-file", cfg.rerank_hf_file, "--reranking",
                ],
            )  # fmt: skip
            await self.embed_pool.start()
            await self.rerank_pool.start()
            # One consumer of each pool for the process's whole life: check one worker out and keep it.
            embed_url = (await self.embed_pool.acquire()).endpoint.base_url
            rerank_url = (await self.rerank_pool.acquire()).endpoint.base_url
        else:
            embed_url, rerank_url = cfg.embed_server_url, cfg.rerank_server_url
        self.engine = LlamaEngine(embed_server_url=embed_url, rerank_server_url=rerank_url)
        try:
            await self.engine.warmup()
        except Exception as exc:  # noqa: BLE001 - warmup is best-effort: the first request pays instead
            logger.info("Inference warmup skipped (%s)", exc)

    async def embed(
        self, items: list[str | list[str | bytes]], *, query: bool
    ) -> list[list[float]]:
        try:
            return await self._engine().embed_batch(
                items, query=query, instruction=self.settings.query_instruction
            )
        except EngineError as exc:
            raise _failure(exc) from exc

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        try:
            return await self._engine().rerank(query, documents)
        except EngineError as exc:
            raise _failure(exc) from exc

    def _engine(self) -> LlamaEngine:
        if self.engine is None:
            raise ServiceUnavailableError("the inference engine is not started")
        return self.engine

    async def aclose(self) -> None:
        if self.engine is not None:
            await self.engine.aclose()
        for pool in (self.embed_pool, self.rerank_pool):
            if pool is not None:
                await pool.aclose()


async def _device(ledger: VramLedger, hardware, budget_mib: int, label: str) -> str:
    """The first GPU if its free memory covers `budget_mib` once reserved, else CPU (never risk an OOM crash)."""
    if not hardware.gpus:
        return "cpu"
    device = f"gpu:{hardware.gpus[0].index}"
    if await ledger.reserve(device, budget_mib) is None:
        logger.warning(
            "inference: the %s server on %s needs about %d MiB of free memory; running it on CPU",
            label, device, budget_mib,
        )  # fmt: skip
        return "cpu"
    return device


def _failure(exc: Exception) -> Exception:
    """Too long an input is the caller's to fix (400); anything else means the model server is unavailable (503)."""
    text = str(exc)
    if "exceeds the available context" in text or "too long" in text.lower():
        return ContextLengthError(f"input exceeds the maximum context length: {text[:300]}")
    return ServiceUnavailableError(text[:300])


def inference(container: Container) -> InferenceService:
    """Provider."""
    service = InferenceService(container.settings.inference)
    container.on_close(service.aclose)
    return service
