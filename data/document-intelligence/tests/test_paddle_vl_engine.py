"""``PaddleVLEngine`` (modes ``vl_cpu``/``vl_gpu``) — mocks the pool AND
``paddlex.inference.pipelines.create_pipeline`` so this exercises the
engine's own logic (pool acquire/release pairing, per-worker pipeline
caching, config-dict shape, error handling) without needing a real GPU,
real llama-server, or real model weights."""

from __future__ import annotations

import asyncio

import pytest

from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.runtimes.document_intelligence.service.engines.paddle_vl import (
    PaddleVLEngine,
    _apply_paddle_patches_once,
    _build_pipeline_config,
)
from substrate.runtimes.inference_pool.pool_types import PoolWorker


class _FakeBlock:
    def __init__(self, label, content="", image=None, bbox=(0.0, 0.0, 1.0, 1.0)):
        self.label = label
        self.content = content
        self.image = image
        self.bbox = bbox


class _FakeResult(dict):
    """Same shape as test_pipeline.py's fixture — verified this session
    that PaddleOCR-VL-1.6's predict() yields the same result shape as
    PPStructureV3's, so the fake is legitimately shared, not coincidental."""

    def __init__(self, *, page_index=0, boxes=None, blocks=None, markdown_texts=""):
        super().__init__(
            page_index=page_index,
            layout_det_res={"boxes": boxes or []},
            parsing_res_list=blocks or [],
        )
        self._markdown_texts = markdown_texts

    @property
    def markdown(self):
        return {
            "markdown_texts": self._markdown_texts,
            "page_continuation_flags": (True, True),
        }


class _FakePaddlexPipeline:
    """Stands in for the real object create_pipeline() would return."""

    def __init__(self, results=None):
        self._results = results or [
            _FakeResult(
                blocks=[_FakeBlock("text", content="hello from the VL model")],
                markdown_texts="hello from the VL model",
            )
        ]
        self.predict_calls: list[tuple[str, dict]] = []

    def predict(self, path, **kwargs):
        self.predict_calls.append((path, kwargs))
        return list(self._results)

    def concatenate_markdown_pages(self, markdown_pages):
        return {
            "markdown_texts": "\n\n".join(
                p["markdown_texts"] for p in markdown_pages
            )
        }


class _FakePool:
    """Minimal InferencePool test double — one worker, records
    acquire/release pairing."""

    def __init__(self, worker_count: int = 1, ready: bool = True):
        self.ready = ready
        self.worker_count = worker_count
        self.acquired: list[int] = []
        self.released: list[int] = []
        self._workers = [
            PoolWorker(
                index=i,
                endpoint=InferenceEndpoint(
                    model="compatible/PaddleOCR-VL-1.6",
                    base_url=f"http://127.0.0.1:{8090 + i}",
                ),
                layout_device=f"gpu:{i}",
            )
            for i in range(worker_count)
        ]
        self._queue: asyncio.Queue = asyncio.Queue()
        for w in self._workers:
            self._queue.put_nowait(w)

    async def start(self) -> None:
        pass

    async def acquire(self) -> PoolWorker:
        worker = await self._queue.get()
        self.acquired.append(worker.index)
        return worker

    def release(self, worker: PoolWorker) -> None:
        self.released.append(worker.index)
        self._queue.put_nowait(worker)

    async def aclose(self) -> None:
        pass


def _patch_create_pipeline(monkeypatch, fake_pipeline: _FakePaddlexPipeline):
    """create_pipeline is imported lazily inside _pipeline_for — patch it
    at its real module so the lazy `from paddlex... import create_pipeline`
    picks up the fake."""
    import paddlex.inference.pipelines as paddlex_pipelines

    captured_config: dict = {}

    def _fake_create_pipeline(*, config, device):
        captured_config["config"] = config
        captured_config["device"] = device
        return fake_pipeline

    monkeypatch.setattr(paddlex_pipelines, "create_pipeline", _fake_create_pipeline)
    return captured_config


async def test_aextract_acquires_and_releases_the_same_worker(monkeypatch) -> None:
    pool = _FakePool(worker_count=1)
    fake_pipeline = _FakePaddlexPipeline()
    _patch_create_pipeline(monkeypatch, fake_pipeline)
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._apply_paddle_patches_once",
        lambda: None,
    )

    engine = PaddleVLEngine(pool, device_mode="gpu")
    result = await engine.aextract(b"%PDF-1.4\n%%EOF", "doc.pdf")

    assert pool.acquired == [0]
    assert pool.released == [0]
    assert result.engine == "paddleocr-vl"
    assert result.pages[0].text == "hello from the VL model"


async def test_pipeline_is_cached_per_worker_not_rebuilt_per_call(monkeypatch) -> None:
    pool = _FakePool(worker_count=1)
    fake_pipeline = _FakePaddlexPipeline()
    build_count = {"n": 0}

    import paddlex.inference.pipelines as paddlex_pipelines

    def _counting_create_pipeline(*, config, device):
        build_count["n"] += 1
        return fake_pipeline

    monkeypatch.setattr(paddlex_pipelines, "create_pipeline", _counting_create_pipeline)
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._apply_paddle_patches_once",
        lambda: None,
    )

    engine = PaddleVLEngine(pool, device_mode="gpu")
    await engine.aextract(b"%PDF-1.4\n%%EOF", "a.pdf")
    await engine.aextract(b"%PDF-1.4\n%%EOF", "b.pdf")

    assert build_count["n"] == 1, "pipeline should be built once and reused per worker"
    assert len(fake_pipeline.predict_calls) == 2


async def test_worker_released_even_when_predict_raises(monkeypatch) -> None:
    pool = _FakePool(worker_count=1)

    import paddlex.inference.pipelines as paddlex_pipelines

    class _RaisingPipeline:
        def predict(self, path, **kwargs):
            raise RuntimeError(
                "The model produced output that does not match the expected "
                "peg-native format"
            )

    monkeypatch.setattr(
        paddlex_pipelines, "create_pipeline", lambda **kw: _RaisingPipeline()
    )
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._apply_paddle_patches_once",
        lambda: None,
    )

    engine = PaddleVLEngine(pool, device_mode="gpu")
    with pytest.raises(RuntimeError, match="peg-native"):
        await engine.aextract(b"%PDF-1.4\n%%EOF", "doc.pdf")

    # Released despite the failure -- a `finally`, not an if-success branch.
    assert pool.released == [0]


async def test_raises_clearly_when_pool_has_zero_healthy_workers() -> None:
    pool = _FakePool(worker_count=0, ready=False)
    engine = PaddleVLEngine(pool, device_mode="gpu")

    with pytest.raises(RuntimeError, match="no healthy inference workers"):
        await engine.aextract(b"%PDF-1.4\n%%EOF", "doc.pdf")


def test_build_pipeline_config_sets_verified_batch_sizes_and_server_url() -> None:
    cfg = _build_pipeline_config(
        server_url="http://127.0.0.1:8090/v1",
        layout_batch_size=4,
        doc_preproc_batch_size=4,
    )
    assert cfg["batch_size"] == 4
    assert cfg["SubModules"]["LayoutDetection"]["batch_size"] == 4
    assert cfg["SubPipelines"]["DocPreprocessor"]["batch_size"] == 4
    assert (
        cfg["SubPipelines"]["DocPreprocessor"]["SubModules"]["DocOrientationClassify"][
            "batch_size"
        ]
        == 4
    )
    vl_config = cfg["SubModules"]["VLRecognition"]["genai_config"]
    assert vl_config["backend"] == "llama-cpp-server"
    assert vl_config["server_url"] == "http://127.0.0.1:8090/v1"
    assert vl_config["max_concurrency"] == 1


async def test_aextract_passes_worker_endpoint_and_device_into_pipeline_config(
    monkeypatch,
) -> None:
    pool = _FakePool(worker_count=1)
    fake_pipeline = _FakePaddlexPipeline()
    captured = _patch_create_pipeline(monkeypatch, fake_pipeline)
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._apply_paddle_patches_once",
        lambda: None,
    )

    engine = PaddleVLEngine(pool, device_mode="gpu")
    await engine.aextract(b"%PDF-1.4\n%%EOF", "doc.pdf")

    assert captured["device"] == "gpu:0"
    assert (
        captured["config"]["SubModules"]["VLRecognition"]["genai_config"]["server_url"]
        == "http://127.0.0.1:8090"
    )


async def test_aextract_batch_processes_all_items_with_bounded_concurrency(
    monkeypatch,
) -> None:
    pool = _FakePool(worker_count=2)
    fake_pipeline = _FakePaddlexPipeline()
    _patch_create_pipeline(monkeypatch, fake_pipeline)
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._apply_paddle_patches_once",
        lambda: None,
    )

    engine = PaddleVLEngine(pool, device_mode="gpu")
    items = [(b"%PDF-1.4\n%%EOF", f"doc{i}.pdf") for i in range(4)]
    results = await engine.aextract_batch(items)

    assert len(results) == 4
    assert all(r.engine == "paddleocr-vl" for r in results)


def test_apply_paddle_patches_once_is_idempotent(monkeypatch) -> None:
    calls = {"disable": 0, "parallelize": 0}
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._disable_mkldnn",
        lambda: calls.__setitem__("disable", calls["disable"] + 1),
    )
    monkeypatch.setattr(
        "substrate.runtimes.document_intelligence.service.engines.paddle_vl._parallelize_crop_image_regions",
        lambda: calls.__setitem__("parallelize", calls["parallelize"] + 1),
    )
    import substrate.runtimes.document_intelligence.service.engines.paddle_vl as vl_mod

    monkeypatch.setattr(vl_mod, "_PATCHES_APPLIED", False)
    _apply_paddle_patches_once()
    _apply_paddle_patches_once()

    assert calls == {"disable": 1, "parallelize": 1}


def test_supported_formats_and_accepts() -> None:
    engine = PaddleVLEngine(_FakePool(), device_mode="gpu")
    assert engine.supported_formats() == {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    assert engine.accepts("doc.pdf", "application/pdf")
    assert not engine.accepts("doc.docx", "application/msword")


def test_warmup_never_raises_even_when_pool_not_ready() -> None:
    engine = PaddleVLEngine(_FakePool(worker_count=0, ready=False), device_mode="gpu")
    engine.warmup()  # must not raise


async def test_aclose_delegates_to_pool() -> None:
    pool = _FakePool()
    closed = {"flag": False}

    async def _fake_aclose():
        closed["flag"] = True

    pool.aclose = _fake_aclose
    engine = PaddleVLEngine(pool, device_mode="gpu")
    await engine.aclose()
    assert closed["flag"] is True
