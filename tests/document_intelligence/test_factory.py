"""``engines/factory.py::build_engine`` — the wiring that turns a resolved
runtime into a concrete engine. Mocks model provisioning and pool
construction so this never needs real GPU/network/subprocess access; the
individual pieces it wires together (``ensure_models``,
``LocalLlamaServerPool``, ``RemoteInferencePool``, each concrete engine)
are already covered by their own test files."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from substrate.runtimes.document_intelligence.service.autoconfig import ResolvedRuntime
from substrate.runtimes.document_intelligence.service.engines import factory
from substrate.runtimes.document_intelligence.service.engines.paddle_vl import PaddleVLEngine
from substrate.runtimes.document_intelligence.service.engines.raw_text import RawTextEngine


@dataclass
class _FakeCfg:
    mode: str = "auto"
    device: str | None = None
    ocr_size: str = "tiny"
    ocr_batch_size: int = 16
    vl_remote_base_url: str | None = None
    vl_model_dir: str = "/models/paddleocr-vl"
    llama_server_bin: str = "llama-server"
    llama_quantize_bin: str = "llama-quantize"
    vl_hf_repo: str = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
    vl_base_port: int = 8090


async def test_raw_text_mode_builds_raw_text_engine() -> None:
    resolved = ResolvedRuntime(mode="raw_text", worker_count=0)
    engine = await factory.build_engine(_FakeCfg(), resolved)
    assert isinstance(engine, RawTextEngine)


async def test_ocr_classic_mode_uses_resolved_device_when_cfg_device_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class _FakeClassicEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(factory, "PaddleClassicEngine", _FakeClassicEngine)
    resolved = ResolvedRuntime(
        mode="ocr_classic", worker_count=1, layout_device_for_worker=["gpu:0"]
    )
    await factory.build_engine(_FakeCfg(), resolved)

    assert captured["device"] == "gpu:0"
    assert captured["ocr_size"] == "tiny"
    assert captured["ocr_batch_size"] == 16


async def test_ocr_classic_mode_explicit_cfg_device_wins_over_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-level-explicit-always-wins precedence (autoconfig.py's
    resolve_child_setting rule), applied manually here since ocr_classic's
    device isn't routed through that function."""
    captured = {}

    class _FakeClassicEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(factory, "PaddleClassicEngine", _FakeClassicEngine)
    resolved = ResolvedRuntime(
        mode="ocr_classic", worker_count=1, layout_device_for_worker=["gpu:0"]
    )
    await factory.build_engine(_FakeCfg(device="cpu"), resolved)

    assert captured["device"] == "cpu"


async def test_vl_mode_with_remote_base_url_builds_remote_pool_no_model_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fail_ensure_models(*a, **kw):
        raise AssertionError("ensure_models must not be called for a remote endpoint")

    monkeypatch.setattr(factory, "ensure_models", _fail_ensure_models)

    resolved = ResolvedRuntime(
        mode="vl_gpu", worker_count=1, layout_device_for_worker=["gpu:0"]
    )
    engine = await factory.build_engine(
        _FakeCfg(vl_remote_base_url="http://remote-vl:8090"), resolved
    )

    assert isinstance(engine, PaddleVLEngine)


async def test_vl_mode_local_provisions_models_then_starts_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_models_calls = []

    async def _fake_ensure_models(model_dir, *, quant, llama_quantize_bin, hf_repo):
        ensure_models_calls.append((model_dir, quant))
        return "/models/main.gguf", "/models/mmproj.gguf"

    class _FakeLocalPool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.ready = True
            self.worker_count = 1
            self.started = False

        async def start(self):
            self.started = True

    built_pools = []

    def _fake_pool_ctor(**kwargs):
        pool = _FakeLocalPool(**kwargs)
        built_pools.append(pool)
        return pool

    monkeypatch.setattr(factory, "ensure_models", _fake_ensure_models)
    monkeypatch.setattr(factory, "LocalLlamaServerPool", _fake_pool_ctor)

    resolved = ResolvedRuntime(
        mode="vl_gpu",
        worker_count=1,
        layout_device_for_worker=["gpu:0"],
        quant="q8_0",
        llama_ngl=99,
        llama_slots=1,
        llama_ctx=6144,
    )
    engine = await factory.build_engine(_FakeCfg(), resolved)

    assert ensure_models_calls == [("/models/paddleocr-vl", "q8_0")]
    assert built_pools[0].started is True
    assert built_pools[0].kwargs["main_gguf"] == "/models/main.gguf"
    assert built_pools[0].kwargs["mmproj_gguf"] == "/models/mmproj.gguf"
    assert isinstance(engine, PaddleVLEngine)


async def test_unknown_mode_raises_value_error() -> None:
    resolved = ResolvedRuntime(mode="not-a-real-mode", worker_count=0)
    with pytest.raises(ValueError, match="Unknown resolved runtime mode"):
        await factory.build_engine(_FakeCfg(), resolved)
