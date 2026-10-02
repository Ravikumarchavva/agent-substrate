"""Embedding-reranker service routes — the OpenAI embeddings wire and the Jina/Cohere rerank wire — against a fake engine on app.state, never
the real llama-embed/llama-rerank sidecars. A bare FastAPI app with no lifespan, so constructing it never makes a real HTTP call."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedding_reranker.llama import EngineError
from embedding_reranker.routes import router


@dataclass
class _FakeConfig:
    auth_token: str = ""
    pod_name: str = "embedding-reranker-test"
    embedding_dim: int = 3
    query_instruction: str = "find it"
    local_ctx_size: int = 2048


class _FakeEngine:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[list, bool, str]] = []
        self._error = error

    async def embed_batch(self, items, *, query=False, instruction=""):
        if self._error:
            raise self._error
        self.calls.append((list(items), query, instruction))
        return [[float(i), 0.5, 0.25] for i in range(len(items))]

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        if self._error:
            raise self._error
        return [0.1 * (i + 1) for i in range(len(passages))]


def _client(*, engine=None, config=None) -> tuple[TestClient, _FakeEngine]:
    app = FastAPI()
    app.include_router(router)
    app.state.engine = engine or _FakeEngine()
    app.state.config = config or _FakeConfig()
    app.state.start_time = time.monotonic()
    return TestClient(app), app.state.engine


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ── /v1/embeddings ───────────────────────────────────────────────────────────


def test_embeddings_answers_one_row_per_input_in_order():
    client, engine = _client()
    resp = client.post("/v1/embeddings", json={"input": ["alpha", "beta"], "model": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list" and [row["index"] for row in body["data"]] == [0, 1]
    assert body["data"][1]["embedding"] == [1.0, 0.5, 0.25] and body["model"]
    assert engine.calls == [(["alpha", "beta"], False, "find it")]


def test_a_query_is_flagged_so_the_engine_applies_the_instruction():
    client, engine = _client()
    client.post("/v1/embeddings", json={"input": ["what is the refund policy"], "input_type": "query"})
    assert engine.calls[0][1] is True


def test_images_and_mixed_items_reach_the_engine_as_bytes_and_part_lists():
    client, engine = _client()
    resp = client.post(
        "/v1/embeddings",
        json={"input": [{"image": _b64(b"png")}, {"content": ["a chart", {"image": _b64(b"png2")}]}, "text"]},
    )
    assert resp.status_code == 200
    items = engine.calls[0][0]
    assert items[0] == [b"png"] and items[1] == ["a chart", b"png2"] and items[2] == "text"


def test_a_bad_input_is_a_400():
    client, _ = _client()
    assert client.post("/v1/embeddings", json={"input": [{"image": "not base64!!"}]}).status_code == 400
    assert client.post("/v1/embeddings", json={"input": [{"nope": 1}]}).status_code == 400
    assert client.post("/v1/embeddings", json={"input": []}).status_code == 422


def test_a_sidecar_that_is_down_is_503_and_an_input_too_long_is_400():
    down, _ = _client(engine=_FakeEngine(error=EngineError("sidecar unreachable")))
    assert down.post("/v1/embeddings", json={"input": ["a"]}).status_code == 503
    assert down.post("/v1/rerank", json={"query": "q", "documents": ["a"]}).status_code == 503
    long, _ = _client(engine=_FakeEngine(error=EngineError("request (3000 tokens) exceeds the available context size (2048 tokens)")))
    assert long.post("/v1/embeddings", json={"input": ["a"]}).status_code == 400


# ── /v1/rerank ───────────────────────────────────────────────────────────────


def test_rerank_answers_a_row_per_document_best_first_with_its_index():
    client, _ = _client()
    body = client.post("/v1/rerank", json={"query": "q", "documents": ["a", "b", "c"]}).json()
    assert [(r["index"], round(r["relevance_score"], 2)) for r in body["results"]] == [(2, 0.3), (1, 0.2), (0, 0.1)]
    top = client.post("/v1/rerank", json={"query": "q", "documents": ["a", "b", "c"], "top_n": 1}).json()
    assert [r["index"] for r in top["results"]] == [2]


# ── /v1/models, /v1/health and auth ───────────────────────────────────────────


def test_models_says_what_the_embedding_model_is():
    client, _ = _client()
    entry = client.get("/v1/models").json()["data"][0]
    assert entry["dimensions"] == 3 and entry["max_input_tokens"] == 2048 and entry["modalities"] == ["text", "image"]


def test_the_library_clients_work_against_this_service():
    """``RemoteEmbedder`` and ``RemoteReranker`` speak these wires: run them against the routes (through a real server)."""
    import threading

    import uvicorn

    from substrate.models.remote import RemoteEmbedder, RemoteReranker

    client, _ = _client()
    config = uvicorn.Config(client.app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    try:
        import asyncio

        async def run():
            embedder = RemoteEmbedder(url)
            result = await embedder.embed(["alpha", "beta"], query=True)
            scores = await RemoteReranker(url).rerank("q", ["a", "b", "c"])
            return embedder, result, scores

        embedder, result, scores = asyncio.run(run())
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert (embedder.dimensions, embedder.max_input_tokens) == (3, 2048) and len(result.embeddings) == 2
    assert [round(s, 2) for s in scores] == [0.1, 0.2, 0.3]


def test_health_has_no_auth_requirement_and_the_token_guards_the_rest():
    client, _ = _client(config=_FakeConfig(auth_token="secret"))
    assert client.get("/v1/health").status_code == 200
    assert client.post("/v1/embeddings", json={"input": ["a"]}).status_code == 401
    assert client.post("/v1/embeddings", json={"input": ["a"]}, headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.post("/v1/embeddings", json={"input": ["a"]}, headers={"Authorization": "Bearer secret"}).status_code == 200
    assert client.get("/v1/models").status_code == 401
