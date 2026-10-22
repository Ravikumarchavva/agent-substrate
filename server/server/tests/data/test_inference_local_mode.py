"""The inference service's start-up: "remote" (servers that already run) vs "local" (it spawns its own
llama-embed/llama-rerank children through `loamz.engine.models.llama`).

Everything here is faked: hardware detection, the pool and the engine; no GPU, subprocess or network is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from loamz.server.data.features.inference import service as service_module
from loamz.server.data.kernel.settings import InferenceSettings

# ── fakes ────────────────────────────────────────────────────────────────


@dataclass
class _FakeGpu:
    index: int = 0
    total_mib: int = 8192
    free_mib: int = 8192


@dataclass
class _FakeHardwareProfile:
    gpus: list = field(default_factory=list)


class _FakeEmbeddingReranker:
    """Records constructor kwargs; warmup/aclose are no-ops so no real
    network call is ever made."""

    instances: list[_FakeEmbeddingReranker] = []

    def __init__(self, *, embed_server_url: str, rerank_server_url: str) -> None:
        self.embed_server_url = embed_server_url
        self.rerank_server_url = rerank_server_url
        self.warmup_called = False
        self.aclose_called = False
        _FakeEmbeddingReranker.instances.append(self)

    async def warmup(self) -> None:
        self.warmup_called = True

    async def aclose(self) -> None:
        self.aclose_called = True


class _FakePoolWorker:
    def __init__(self, base_url: str) -> None:
        self.endpoint = SimpleNamespace(base_url=base_url)


class _FakeLocalLlamaServerPool:
    """Records constructor kwargs and start/acquire/aclose calls; never
    touches a real subprocess."""

    instances: list[_FakeLocalLlamaServerPool] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.acquired = False
        self.closed = False
        _FakeLocalLlamaServerPool.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def acquire(self):
        self.acquired = True
        port = self.kwargs["base_port"]
        return _FakePoolWorker(base_url=f"http://127.0.0.1:{port}")

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakeEmbeddingReranker.instances.clear()
    _FakeLocalLlamaServerPool.instances.clear()
    yield
    _FakeEmbeddingReranker.instances.clear()
    _FakeLocalLlamaServerPool.instances.clear()


async def _started(mode: str = "remote") -> service_module.InferenceService:
    service = service_module.InferenceService(InferenceSettings(mode=mode))
    await service.start()
    return service


# ── remote mode (default) — untouched behavior ──────────────────────────


async def test_remote_mode_never_calls_hardware_detect_or_constructs_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_detect():
        raise AssertionError("hardware.detect() must not be called in remote mode")

    def _fail_pool_ctor(**kwargs):
        raise AssertionError("LocalLlamaServerPool must not be constructed in remote mode")

    monkeypatch.setattr(service_module, "detect", _fail_detect)
    monkeypatch.setattr(service_module, "LlamaEngine", _FakeEmbeddingReranker)

    monkeypatch.setattr(service_module, "LocalLlamaServerPool", _fail_pool_ctor)

    service = await _started()
    try:
        assert service.embed_pool is None
        assert service.rerank_pool is None
        reranker = service.engine
        assert reranker.embed_server_url == "http://llama-embed:8031"
        assert reranker.rerank_server_url == "http://llama-rerank:8032"
        assert reranker.warmup_called is True
    finally:
        await service.aclose()

    assert reranker.aclose_called is True


# ── local mode — sufficient VRAM ────────────────────────────────────────


async def test_local_mode_with_sufficient_vram_constructs_two_pools_and_wires_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _FakeHardwareProfile(gpus=[_FakeGpu(index=0, free_mib=8192)])
    monkeypatch.setattr(service_module, "detect", lambda: profile)
    monkeypatch.setattr(service_module, "LlamaEngine", _FakeEmbeddingReranker)

    monkeypatch.setattr(service_module, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    service = await _started("local")
    try:
        assert len(_FakeLocalLlamaServerPool.instances) == 2
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances
        assert embed_pool.started is True and embed_pool.acquired is True
        assert rerank_pool.started is True and rerank_pool.acquired is True
        # Sufficient VRAM (8192 MiB > 2500 MiB budget each) -- admitted to gpu:0.
        assert embed_pool.kwargs["gpu_devices"] == ["gpu:0"]
        assert rerank_pool.kwargs["gpu_devices"] == ["gpu:0"]

        reranker = service.engine
        assert reranker.embed_server_url == f"http://127.0.0.1:{embed_pool.kwargs['base_port']}"
        assert reranker.rerank_server_url == f"http://127.0.0.1:{rerank_pool.kwargs['base_port']}"
        assert service.embed_pool is embed_pool
        assert service.rerank_pool is rerank_pool
    finally:
        await service.aclose()

    assert embed_pool.closed is True
    assert rerank_pool.closed is True


# ── local mode — insufficient VRAM ──────────────────────────────────────


async def test_local_mode_with_insufficient_vram_degrades_to_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 100 MiB free is below both the embed and rerank 2500 MiB budgets.
    profile = _FakeHardwareProfile(gpus=[_FakeGpu(index=0, free_mib=100)])
    monkeypatch.setattr(service_module, "detect", lambda: profile)
    monkeypatch.setattr(service_module, "LlamaEngine", _FakeEmbeddingReranker)

    monkeypatch.setattr(service_module, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    service = await _started("local")
    try:
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances
        assert embed_pool.kwargs["gpu_devices"] == ["cpu"]
        assert rerank_pool.kwargs["gpu_devices"] == ["cpu"]
    finally:
        await service.aclose()


async def test_local_mode_passes_real_embed_and_rerank_flags_via_extra_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The genuine fix for the gap flagged when local mode first landed:
    LocalLlamaServerPool now takes main_gguf/mmproj_gguf=None + extra_args
    instead of forcing PaddleOCR-VL-shaped -m/--mmproj flags onto an
    embedding/reranking model. Verifies the exact real flags docker-
    compose.yml's llama-embed/llama-rerank sidecars use (--hf-repo/
    --hf-file/--embedding/--pooling last/--reranking) actually reach the
    pool constructor, and that --parallel is NOT duplicated in extra_args
    since `slots` already covers it."""
    profile = _FakeHardwareProfile(gpus=[_FakeGpu(index=0, free_mib=8192)])
    monkeypatch.setattr(service_module, "detect", lambda: profile)
    monkeypatch.setattr(service_module, "LlamaEngine", _FakeEmbeddingReranker)

    monkeypatch.setattr(service_module, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    service = await _started("local")
    try:
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances

        assert embed_pool.kwargs.get("main_gguf") is None
        assert embed_pool.kwargs.get("mmproj_gguf") is None
        assert embed_pool.kwargs["slots"] == 2
        assert embed_pool.kwargs["extra_args"] == [
            "--hf-repo",
            "Rizwan313/Qwen3-VL-Embedding-2B-GGUF",
            "--hf-file",
            "qwen3-vl-embedding-2b-Q4_K_M.gguf",
            "--embedding",
            "--pooling",
            "last",
        ]
        assert "--parallel" not in embed_pool.kwargs["extra_args"]

        assert rerank_pool.kwargs.get("main_gguf") is None
        assert rerank_pool.kwargs.get("mmproj_gguf") is None
        assert rerank_pool.kwargs["slots"] == 1
        assert rerank_pool.kwargs["extra_args"] == [
            "--hf-repo",
            "staralt/Qwen3-VL-Reranker-2B-Q4_K_M-GGUF",
            "--hf-file",
            "qwen3-vl-reranker-2b-q4_k_m-imat.gguf",
            "--reranking",
        ]
        assert "--parallel" not in rerank_pool.kwargs["extra_args"]
    finally:
        await service.aclose()


async def test_local_mode_with_no_gpu_detected_uses_cpu_without_reserving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _FakeHardwareProfile(gpus=[])
    monkeypatch.setattr(service_module, "detect", lambda: profile)
    monkeypatch.setattr(service_module, "LlamaEngine", _FakeEmbeddingReranker)

    monkeypatch.setattr(service_module, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    service = await _started("local")
    try:
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances
        assert embed_pool.kwargs["gpu_devices"] == ["cpu"]
        assert rerank_pool.kwargs["gpu_devices"] == ["cpu"]
    finally:
        await service.aclose()
