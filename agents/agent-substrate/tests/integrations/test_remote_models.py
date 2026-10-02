"""``RemoteReranker`` held to the reranker conformance suite, and ``RemoteEmbedder``'s own wire behaviour, both against a real HTTP server."""

from __future__ import annotations

import base64
import json
from contextlib import ExitStack

import httpx
import pytest

from substrate.models import Modality
from substrate.models.remote import RemoteEmbedder, RemoteReranker
from substrate.testing.conformance.reranker import RerankerConformance
from substrate.types import MediaBlock, TextBlock
from substrate.types.errors import (
    AuthError,
    ContextLengthError,
    PermanentError,
    RateLimitedError,
    ServiceUnavailableError,
    UnsupportedContentError,
)
from tests._http import serve


class RerankService:
    def __init__(self) -> None:
        self._stack = ExitStack()

    def reranker(self, handler):
        return RemoteReranker(
            self._stack.enter_context(serve(handler)), model="conformance-rerank"
        )

    def passages_in(self, request):
        return json.loads(request.content)["documents"]

    def scores_response(self, scores, *, shuffled=False):
        rows = [{"index": i, "relevance_score": s} for i, s in enumerate(scores)]
        if shuffled:
            rows = rows[::-1]
        return httpx.Response(200, json={"results": rows})

    def error_response(self, status, message):
        return httpx.Response(status, json={"error": message})

    def garbled_response(self):
        return httpx.Response(200, json={"results": [{"nope": 1}]})


class TestRemoteReranker(RerankerConformance):
    @pytest.fixture
    def provider(self):
        provider = RerankService()
        yield provider
        provider._stack.close()


def _vectors(request, width=3):
    items = json.loads(request.content)["input"]
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": i, "embedding": [float(i)] * width} for i in range(len(items))
            ],
            "usage": {"total_tokens": len(items)},
        },
    )


async def test_the_embedder_reads_what_the_model_is_from_v1_models_and_sends_queries_as_queries():
    seen = []

    def handler(request):
        seen.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
                request.headers.get("authorization"),
            )
        )
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3-vl-embedding",
                            "dimensions": 2048,
                            "max_input_tokens": 8192,
                            "modalities": ["text", "image"],
                        }
                    ]
                },
            )
        return _vectors(request, width=2048)

    with serve(handler) as url:
        embedder = RemoteEmbedder(url, api_key="secret")
        await embedder.embed(["what is the refund policy"], query=True)
        await embedder.embed(["the policy text"])
    assert (embedder.model, embedder.dimensions, embedder.max_input_tokens) == (
        "qwen3-vl-embedding",
        2048,
        8192,
    ) and Modality.IMAGE in embedder.modalities
    calls = [c for c in seen if c[1] == "/v1/embeddings"]
    assert (
        calls[0][2]["input_type"] == "query"
        and "input_type" not in calls[1][2]
        and calls[0][3] == "Bearer secret"
    )
    assert sum(1 for c in seen if c[1] == "/v1/models") == 1  # described once


async def test_an_embedder_without_v1_models_still_works_and_learns_its_width():
    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/v1/models"
            else _vectors(request, width=5)
        )

    with serve(handler) as url:
        embedder = RemoteEmbedder(url, model="m")
        result = await embedder.embed(["a", "b"])
    assert (
        embedder.dimensions == 5
        and len(result.embeddings) == 2
        and embedder.model == "m"
    )


async def test_images_and_mixed_items_go_over_the_wire_only_to_a_model_that_takes_them():
    bodies = []

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "vl", "modalities": ["text", "image"]}]}
            )
        bodies.append(json.loads(request.content))
        return _vectors(request)

    png = MediaBlock.image(data=b"\x89PNG-bytes", media_type="image/png")
    with serve(handler) as url:
        embedder = RemoteEmbedder(url)
        await embedder.embed(
            [
                [png],
                [TextBlock(text="a chart of revenue"), png],
                [TextBlock(text="a "), TextBlock(text="caption")],
            ]
        )
    items = bodies[0]["input"]
    assert (
        items[0] == {"image": base64.b64encode(b"\x89PNG-bytes").decode()}
        and items[1]["content"][0] == "a chart of revenue"
        and items[1]["content"][1] == items[0]
        and items[2] == "a caption"
    )

    def text_only(request):
        return (
            httpx.Response(404)
            if request.url.path == "/v1/models"
            else _vectors(request)
        )

    with serve(text_only) as url:
        with pytest.raises(UnsupportedContentError):
            await RemoteEmbedder(url).embed([[png]])


async def test_a_large_batch_is_split_and_reassembled_in_order():
    sizes = []

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(404)
        items = json.loads(request.content)["input"]
        sizes.append(len(items))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [float(item.split()[-1])]}
                    for i, item in reversed(list(enumerate(items)))
                ]
            },
        )  # answered out of order

    with serve(handler) as url:
        result = await RemoteEmbedder(url, batch_size=10).embed(
            [f"text {n}" for n in range(25)]
        )
    assert sizes == [10, 10, 5] and [v[0] for v in result.embeddings] == [
        float(n) for n in range(25)
    ]


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (503, "down", ServiceUnavailableError),
        (429, "slow down", RateLimitedError),
        (401, "no", AuthError),
        (400, "input exceeds the maximum context length", ContextLengthError),
        (400, "bad", PermanentError),
    ],
)
async def test_what_goes_wrong_is_a_typed_error(status, body, error):
    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/v1/models"
            else httpx.Response(status, text=body)
        )

    with serve(handler) as url:
        with pytest.raises(error):
            await RemoteEmbedder(url).embed(["a"])
        with pytest.raises(error):
            await RemoteReranker(url).rerank("q", ["a"])


async def test_a_server_that_is_not_there_is_service_unavailable_and_a_short_answer_is_refused():
    with serve(lambda request: httpx.Response(404)) as url:
        pass  # the server is closed now
    with pytest.raises(ServiceUnavailableError):
        await RemoteEmbedder(url).embed(["a"])

    def short(request):
        return (
            httpx.Response(404)
            if request.url.path == "/v1/models"
            else httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        )

    with serve(short) as url:
        with pytest.raises(PermanentError, match="1 embeddings for 2 inputs"):
            await RemoteEmbedder(url).embed(["a", "b"])
