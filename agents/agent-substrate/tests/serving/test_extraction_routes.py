"""Extraction service routes — exercised against fake pipeline/embedding
objects on app.state, never the real paddleocr/sentence-transformers models
(those are covered by test_extraction_pipeline.py / test_extraction_embedding.py).
A bare FastAPI app with no lifespan is built here so constructing it never
touches the heavy `extraction` extra at all."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from substrate.serving.services.extraction.pipeline import ExtractedImage, ExtractedPage
from substrate.serving.services.extraction.routes import router


@dataclass
class _FakeConfig:
    auth_token: str = ""
    max_upload_bytes: int = 50 * 1024 * 1024
    pod_name: str = "extraction-test"


class _FakePipeline:
    def __init__(
        self, pages: list[ExtractedPage] | None = None, error: Exception | None = None
    ):
        self._pages = pages if pages is not None else []
        self._error = error

    def extract(self, data: bytes, filename: str) -> list[ExtractedPage]:
        if self._error is not None:
            raise self._error
        return self._pages


class _FakeEmbeddingReranker:
    def __init__(self):
        self.embed_image_calls: list[bytes] = []
        self.embed_text_calls: list[str] = []

    def embed_image(self, data: bytes) -> list[float]:
        self.embed_image_calls.append(data)
        return [0.1, 0.2, 0.3]

    def embed_text(self, text: str) -> list[float]:
        self.embed_text_calls.append(text)
        return [0.4, 0.5, 0.6]

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return [1.0 - i * 0.1 for i in range(len(passages))]


def _client(
    *, pipeline: _FakePipeline | None = None, embedding_reranker=None, config=None
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.pipeline = pipeline or _FakePipeline()
    app.state.embedding_reranker = embedding_reranker or _FakeEmbeddingReranker()
    app.state.config = config or _FakeConfig()
    app.state.start_time = time.monotonic()
    return TestClient(app)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ── /v1/extract ──────────────────────────────────────────────────────────────


def test_extract_success_returns_pages_and_images():
    pages = [
        ExtractedPage(
            page_number=1,
            text="hello world",
            images=[ExtractedImage(data=b"png-bytes", label="chart", confidence=0.97)],
        )
    ]
    client = _client(pipeline=_FakePipeline(pages=pages))

    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": _b64(b"fake pdf bytes"),
            "filename": "test.pdf",
            "content_type": "application/pdf",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["text"] == "hello world"
    assert body["pages"] == [{"page_number": 1, "text": "hello world"}]
    assert len(body["images"]) == 1
    assert body["images"][0]["label"] == "chart"
    assert base64.b64decode(body["images"][0]["data_base64"]) == b"png-bytes"


def test_extract_unsupported_content_type_returns_400():
    client = _client()
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": _b64(b"data"),
            "filename": "report.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        },
    )
    assert resp.status_code == 400


def test_extract_invalid_base64_returns_400():
    client = _client()
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": "not-valid-base64!!!",
            "filename": "test.pdf",
            "content_type": "application/pdf",
        },
    )
    assert resp.status_code == 400


def test_extract_oversized_file_returns_413():
    client = _client(config=_FakeConfig(max_upload_bytes=4))
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": _b64(b"way too big"),
            "filename": "test.pdf",
            "content_type": "application/pdf",
        },
    )
    assert resp.status_code == 413


def test_extract_pipeline_exception_returns_structured_failure_not_500():
    client = _client(pipeline=_FakePipeline(error=RuntimeError("mkldnn boom")))
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": _b64(b"data"),
            "filename": "test.pdf",
            "content_type": "application/pdf",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "mkldnn boom" in body["error"]


def test_extract_empty_result_returns_structured_failure():
    client = _client(
        pipeline=_FakePipeline(pages=[ExtractedPage(page_number=1, text="")])
    )
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": _b64(b"data"),
            "filename": "test.pdf",
            "content_type": "application/pdf",
        },
    )
    body = resp.json()
    assert body["success"] is False


# ── /v1/embed ────────────────────────────────────────────────────────────────


def test_embed_image_calls_embed_image():
    reranker = _FakeEmbeddingReranker()
    client = _client(embedding_reranker=reranker)
    resp = client.post("/v1/embed", json={"image_base64": _b64(b"png bytes")})

    assert resp.status_code == 200
    assert resp.json()["embedding"] == [0.1, 0.2, 0.3]
    assert reranker.embed_image_calls == [b"png bytes"]


def test_embed_text_calls_embed_text():
    reranker = _FakeEmbeddingReranker()
    client = _client(embedding_reranker=reranker)
    resp = client.post("/v1/embed", json={"text": "revenue chart"})

    assert resp.status_code == 200
    assert resp.json()["embedding"] == [0.4, 0.5, 0.6]
    assert reranker.embed_text_calls == ["revenue chart"]


def test_embed_both_set_returns_400():
    client = _client()
    resp = client.post("/v1/embed", json={"image_base64": _b64(b"x"), "text": "y"})
    assert resp.status_code == 400


def test_embed_neither_set_returns_400():
    client = _client()
    resp = client.post("/v1/embed", json={})
    assert resp.status_code == 400


# ── /v1/rerank ───────────────────────────────────────────────────────────────


def test_rerank_returns_one_score_per_passage():
    client = _client()
    resp = client.post(
        "/v1/rerank", json={"query": "revenue", "passages": ["a", "b", "c"]}
    )
    assert resp.status_code == 200
    assert resp.json()["scores"] == [1.0, 0.9, 0.8]


# ── /v1/health ───────────────────────────────────────────────────────────────


def test_health_returns_ok():
    client = _client(config=_FakeConfig(pod_name="extraction-7"))
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pod_name"] == "extraction-7"
    assert body["uptime_seconds"] >= 0


# ── auth (/v1/health is deliberately unauthenticated — used for k8s
# liveness/readiness probes, which don't send a Bearer token — so these
# exercise /v1/rerank, an Authed route, instead) ───────────────────────────


def test_health_has_no_auth_requirement_even_when_token_configured():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.get("/v1/health")
    assert resp.status_code == 200


def test_missing_auth_token_rejected_when_configured():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post("/v1/rerank", json={"query": "q", "passages": ["a"]})
    assert resp.status_code == 401


def test_wrong_auth_token_rejected():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post(
        "/v1/rerank",
        json={"query": "q", "passages": ["a"]},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 403


def test_correct_auth_token_accepted():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post(
        "/v1/rerank",
        json={"query": "q", "passages": ["a"]},
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
