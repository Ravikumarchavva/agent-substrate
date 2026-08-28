"""Document-intelligence service routes — exercised against a fake pipeline
on app.state, never the real paddleocr model (that's covered by
test_pipeline.py). A bare FastAPI app with no lifespan is built here so
constructing it never touches the heavy `document-intelligence` extra at all."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from substrate.runtimes.document_intelligence.service.pipeline import (
    ExtractedImage,
    ExtractedPage,
    ExtractionResult,
)
from substrate.runtimes.document_intelligence.service.routes import router


@dataclass
class _FakeConfig:
    auth_token: str = ""
    max_upload_bytes: int = 50 * 1024 * 1024
    pod_name: str = "document-intelligence-test"


class _FakePipeline:
    def __init__(
        self, pages: list[ExtractedPage] | None = None, error: Exception | None = None
    ):
        self._pages = pages if pages is not None else []
        self._error = error

    def extract(self, data: bytes, filename: str) -> ExtractionResult:
        if self._error is not None:
            raise self._error
        return ExtractionResult(pages=self._pages)

    def extract_batch(self, items: list[tuple[bytes, str]]) -> list[ExtractionResult]:
        if self._error is not None:
            raise self._error
        return [ExtractionResult(pages=self._pages) for _ in items]


def _client(*, pipeline: _FakePipeline | None = None, config=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.pipeline = pipeline or _FakePipeline()
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
    assert body["pages"] == [{"page_number": 1, "text": "hello world", "markdown": ""}]
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


# ── /v1/extract-batch ───────────────────────────────────────────────────────


def _item(filename: str = "test.pdf", data: bytes = b"data") -> dict:
    return {
        "content_base64": _b64(data),
        "filename": filename,
        "content_type": "application/pdf",
    }


def test_extract_batch_success_returns_one_response_per_item():
    pages = [ExtractedPage(page_number=1, text="hello world")]
    client = _client(pipeline=_FakePipeline(pages=pages))

    resp = client.post(
        "/v1/extract-batch",
        json={"items": [_item("a.pdf"), _item("b.pdf")]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(item["success"] is True for item in body)
    assert all(item["text"] == "hello world" for item in body)


def test_extract_batch_empty_items_returns_empty_list():
    client = _client()
    resp = client.post("/v1/extract-batch", json={"items": []})
    assert resp.status_code == 200
    assert resp.json() == []


def test_extract_batch_partial_failure_does_not_fail_whole_batch():
    """One item with a bad content_type must not 400 (or otherwise fail)
    the other, valid items in the same batch -- the whole point of the
    per-item soft-failure design over /extract's stricter single-file
    behavior."""
    pages = [ExtractedPage(page_number=1, text="hello world")]
    client = _client(pipeline=_FakePipeline(pages=pages))

    bad_item = _item("bad.docx")
    bad_item["content_type"] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    resp = client.post(
        "/v1/extract-batch",
        json={"items": [_item("good.pdf"), bad_item]},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["success"] is True
    assert body[1]["success"] is False
    assert "Unsupported content_type" in body[1]["error"]


def test_extract_batch_preserves_input_order_with_mixed_results():
    pages = [ExtractedPage(page_number=1, text="hello world")]
    client = _client(pipeline=_FakePipeline(pages=pages))

    bad_item = _item("bad.docx")
    bad_item["content_type"] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    resp = client.post(
        "/v1/extract-batch",
        json={"items": [bad_item, _item("good.pdf"), bad_item]},
    )

    body = resp.json()
    assert [item["success"] for item in body] == [False, True, False]


def test_extract_batch_pipeline_exception_fails_only_validated_items():
    client = _client(pipeline=_FakePipeline(error=RuntimeError("mkldnn boom")))

    bad_item = _item("bad.docx")
    bad_item["content_type"] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    resp = client.post(
        "/v1/extract-batch",
        json={"items": [_item("good.pdf"), bad_item]},
    )

    body = resp.json()
    assert resp.status_code == 200
    # The validation-failed item keeps its own specific error, not the
    # pipeline exception -- it never reached the pipeline at all.
    assert body[0]["success"] is False
    assert "mkldnn boom" in body[0]["error"]
    assert body[1]["success"] is False
    assert "Unsupported content_type" in body[1]["error"]


def test_extract_batch_oversized_item_returns_413_equivalent_soft_failure():
    client = _client(config=_FakeConfig(max_upload_bytes=4))
    resp = client.post(
        "/v1/extract-batch",
        json={"items": [_item(data=b"way too big")]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["success"] is False
    assert "maximum size" in body[0]["error"]


# ── /v1/health ───────────────────────────────────────────────────────────────


def test_health_returns_ok():
    client = _client(config=_FakeConfig(pod_name="document-intelligence-7"))
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pod_name"] == "document-intelligence-7"
    assert body["uptime_seconds"] >= 0


# ── auth (/v1/health is deliberately unauthenticated — used for k8s
# liveness/readiness probes, which don't send a Bearer token — so these
# exercise /v1/extract, an Authed route, instead) ───────────────────────────


def test_health_has_no_auth_requirement_even_when_token_configured():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.get("/v1/health")
    assert resp.status_code == 200


def _extract_body() -> dict:
    return {
        "content_base64": _b64(b"data"),
        "filename": "test.pdf",
        "content_type": "application/pdf",
    }


def test_missing_auth_token_rejected_when_configured():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post("/v1/extract", json=_extract_body())
    assert resp.status_code == 401


def test_wrong_auth_token_rejected():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post(
        "/v1/extract",
        json=_extract_body(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 403


def test_correct_auth_token_accepted():
    client = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post(
        "/v1/extract",
        json=_extract_body(),
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
