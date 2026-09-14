"""``app.py``'s lifespan — the "remote" (default, pre-existing sidecars) vs
"local" (this process spawns its own llama-embed/llama-rerank children via
the shared ``inference_pool`` machinery) deployment modes.

Everything here is mocked: ``hardware.detect``, ``LocalLlamaServerPool``,
and ``EmbeddingReranker`` itself — no real GPU/subprocess/network access is
exercised, same testing philosophy as
``tests/document_intelligence/test_factory.py``'s pool/model mocking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from substrate.runtimes.embedding_reranker.service import app as app_module


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

    instances: list["_FakeEmbeddingReranker"] = []

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

    instances: list["_FakeLocalLlamaServerPool"] = []

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


def _fake_app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


# ── remote mode (default) — untouched behavior ──────────────────────────


async def test_remote_mode_never_calls_hardware_detect_or_constructs_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_detect():
        raise AssertionError("hardware.detect() must not be called in remote mode")

    def _fail_pool_ctor(**kwargs):
        raise AssertionError("LocalLlamaServerPool must not be constructed in remote mode")

    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    monkeypatch.setattr(hardware_mod, "detect", _fail_detect)
    monkeypatch.setattr(app_module, "EmbeddingReranker", _FakeEmbeddingReranker)

    import substrate.runtimes.inference_pool.llama_pool as llama_pool_mod

    monkeypatch.setattr(llama_pool_mod, "LocalLlamaServerPool", _fail_pool_ctor)

    fake_app = _fake_app()

    async with app_module.lifespan(fake_app):
        assert fake_app.state.embed_pool is None
        assert fake_app.state.rerank_pool is None
        reranker = fake_app.state.embedding_reranker
        assert reranker.embed_server_url == "http://llama-embed:8031"
        assert reranker.rerank_server_url == "http://llama-rerank:8032"
        assert reranker.warmup_called is True

    assert reranker.aclose_called is True


# ── local mode — sufficient VRAM ────────────────────────────────────────


async def test_local_mode_with_sufficient_vram_constructs_two_pools_and_wires_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    profile = _FakeHardwareProfile(gpus=[_FakeGpu(index=0, free_mib=8192)])
    monkeypatch.setattr(hardware_mod, "detect", lambda: profile)
    monkeypatch.setattr(app_module, "EmbeddingReranker", _FakeEmbeddingReranker)

    import substrate.runtimes.inference_pool.llama_pool as llama_pool_mod

    monkeypatch.setattr(llama_pool_mod, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    monkeypatch.setenv("EMBEDDING_RERANKER_MODE", "local")
    fake_app = _fake_app()

    async with app_module.lifespan(fake_app):
        assert len(_FakeLocalLlamaServerPool.instances) == 2
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances
        assert embed_pool.started is True and embed_pool.acquired is True
        assert rerank_pool.started is True and rerank_pool.acquired is True
        # Sufficient VRAM (8192 MiB > 2500 MiB budget each) -- admitted to gpu:0.
        assert embed_pool.kwargs["gpu_devices"] == ["gpu:0"]
        assert rerank_pool.kwargs["gpu_devices"] == ["gpu:0"]

        reranker = fake_app.state.embedding_reranker
        assert reranker.embed_server_url == f"http://127.0.0.1:{embed_pool.kwargs['base_port']}"
        assert reranker.rerank_server_url == f"http://127.0.0.1:{rerank_pool.kwargs['base_port']}"
        assert fake_app.state.embed_pool is embed_pool
        assert fake_app.state.rerank_pool is rerank_pool

    assert embed_pool.closed is True
    assert rerank_pool.closed is True


# ── local mode — insufficient VRAM ──────────────────────────────────────


async def test_local_mode_with_insufficient_vram_degrades_to_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    # 100 MiB free is below both the embed and rerank 2500 MiB budgets.
    profile = _FakeHardwareProfile(gpus=[_FakeGpu(index=0, free_mib=100)])
    monkeypatch.setattr(hardware_mod, "detect", lambda: profile)
    monkeypatch.setattr(app_module, "EmbeddingReranker", _FakeEmbeddingReranker)

    import substrate.runtimes.inference_pool.llama_pool as llama_pool_mod

    monkeypatch.setattr(llama_pool_mod, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    monkeypatch.setenv("EMBEDDING_RERANKER_MODE", "local")
    fake_app = _fake_app()

    async with app_module.lifespan(fake_app):
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances
        assert embed_pool.kwargs["gpu_devices"] == ["cpu"]
        assert rerank_pool.kwargs["gpu_devices"] == ["cpu"]


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
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    profile = _FakeHardwareProfile(gpus=[_FakeGpu(index=0, free_mib=8192)])
    monkeypatch.setattr(hardware_mod, "detect", lambda: profile)
    monkeypatch.setattr(app_module, "EmbeddingReranker", _FakeEmbeddingReranker)

    import substrate.runtimes.inference_pool.llama_pool as llama_pool_mod

    monkeypatch.setattr(llama_pool_mod, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    monkeypatch.setenv("EMBEDDING_RERANKER_MODE", "local")
    fake_app = _fake_app()

    async with app_module.lifespan(fake_app):
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


async def test_local_mode_with_no_gpu_detected_uses_cpu_without_reserving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    profile = _FakeHardwareProfile(gpus=[])
    monkeypatch.setattr(hardware_mod, "detect", lambda: profile)
    monkeypatch.setattr(app_module, "EmbeddingReranker", _FakeEmbeddingReranker)

    import substrate.runtimes.inference_pool.llama_pool as llama_pool_mod

    monkeypatch.setattr(llama_pool_mod, "LocalLlamaServerPool", _FakeLocalLlamaServerPool)

    monkeypatch.setenv("EMBEDDING_RERANKER_MODE", "local")
    fake_app = _fake_app()

    async with app_module.lifespan(fake_app):
        embed_pool, rerank_pool = _FakeLocalLlamaServerPool.instances
        assert embed_pool.kwargs["gpu_devices"] == ["cpu"]
        assert rerank_pool.kwargs["gpu_devices"] == ["cpu"]
