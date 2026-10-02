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


# ── VRAM admission (Phase 5's VramLedger, wired in additively) ──────────────


@dataclass
class _FakeGpu:
    index: int = 0
    total_mib: int = 4096
    free_mib: int = 0


@dataclass
class _FakeHardwareProfile:
    gpus: list


def _mock_hardware_detect(monkeypatch: pytest.MonkeyPatch, *, free_mib: int) -> None:
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    profile = _FakeHardwareProfile(gpus=[_FakeGpu(free_mib=free_mib)])
    monkeypatch.setattr(hardware_mod, "detect", lambda: profile)


class _FakeLocalPool:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.ready = bool(kwargs.get("gpu_devices"))
        self.worker_count = len(kwargs.get("gpu_devices", []))
        self.started = False

    async def start(self):
        self.started = True


async def test_vl_gpu_admits_worker_when_real_free_vram_covers_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_hardware_detect(monkeypatch, free_mib=4000)  # > 3500 MiB budget

    async def _fake_ensure_models(*a, **kw):
        return "/models/main.gguf", "/models/mmproj.gguf"

    built_pools: list[_FakeLocalPool] = []

    def _fake_pool_ctor(**kwargs):
        pool = _FakeLocalPool(**kwargs)
        built_pools.append(pool)
        return pool

    monkeypatch.setattr(factory, "ensure_models", _fake_ensure_models)
    monkeypatch.setattr(factory, "LocalLlamaServerPool", _fake_pool_ctor)

    resolved = ResolvedRuntime(
        mode="vl_gpu", worker_count=1, layout_device_for_worker=["gpu:0"]
    )
    await factory.build_engine(_FakeCfg(), resolved)

    assert built_pools[0].kwargs["gpu_devices"] == ["gpu:0"]


async def test_vl_gpu_degrades_worker_when_real_free_vram_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real, in-scope use of VramLedger: autoconfig's static eligibility
    threshold said this GPU qualified, but the *real*, currently-detected
    free_mib at engine-build time is below this session's verified
    per-worker budget -- admission must drop the worker rather than risk a
    real CUDA OOM crash."""
    _mock_hardware_detect(monkeypatch, free_mib=1000)  # well under 3500 MiB budget

    async def _fake_ensure_models(*a, **kw):
        return "/models/main.gguf", "/models/mmproj.gguf"

    built_pools: list[_FakeLocalPool] = []

    def _fake_pool_ctor(**kwargs):
        pool = _FakeLocalPool(**kwargs)
        built_pools.append(pool)
        return pool

    monkeypatch.setattr(factory, "ensure_models", _fake_ensure_models)
    monkeypatch.setattr(factory, "LocalLlamaServerPool", _fake_pool_ctor)

    resolved = ResolvedRuntime(
        mode="vl_gpu", worker_count=1, layout_device_for_worker=["gpu:0"]
    )
    engine = await factory.build_engine(_FakeCfg(), resolved)

    assert built_pools[0].kwargs["gpu_devices"] == []
    assert built_pools[0].ready is False
    assert isinstance(engine, PaddleVLEngine)  # degrades, never raises


async def test_vl_cpu_mode_never_calls_hardware_detect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No GPU device in layout_device_for_worker -> _admit_gpu_workers'
    early-return path -- hardware.detect() (real subprocess/pynvml calls on
    a real deployment) must not run at all for a pure-CPU worker."""
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    def _fail_if_called():
        raise AssertionError("hardware.detect() must not be called for a CPU worker")

    monkeypatch.setattr(hardware_mod, "detect", _fail_if_called)

    async def _fake_ensure_models(*a, **kw):
        return "/models/main.gguf", "/models/mmproj.gguf"

    monkeypatch.setattr(factory, "ensure_models", _fake_ensure_models)
    monkeypatch.setattr(factory, "LocalLlamaServerPool", lambda **kw: _FakeLocalPool(**kw))

    resolved = ResolvedRuntime(
        mode="vl_cpu", worker_count=1, layout_device_for_worker=["cpu"]
    )
    engine = await factory.build_engine(_FakeCfg(), resolved)

    assert isinstance(engine, PaddleVLEngine)


async def test_remote_endpoint_skips_vram_admission_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote pool's VRAM is someone else's process to arbitrate --
    admission must not run (or matter) for the remote-endpoint path."""
    import substrate.runtimes.document_intelligence.service.hardware as hardware_mod

    def _fail_if_called():
        raise AssertionError("hardware.detect() must not be called for a remote endpoint")

    monkeypatch.setattr(hardware_mod, "detect", _fail_if_called)

    resolved = ResolvedRuntime(
        mode="vl_gpu", worker_count=1, layout_device_for_worker=["gpu:0"]
    )
    engine = await factory.build_engine(
        _FakeCfg(vl_remote_base_url="http://remote-vl:8090"), resolved
    )

    assert isinstance(engine, PaddleVLEngine)
