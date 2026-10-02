"""Every embedding client, held to the embedding-client conformance suite."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest
from openai import AsyncOpenAI

from substrate.integrations.llm.local_embeddings import SentenceTransformersEmbeddingClient
from substrate.integrations.llm.openai.openai_embedding_client import OpenAIEmbeddingClient
from substrate.testing.conformance.embedding_model import EmbeddingModelConformance, vector_of


class OpenAIEmbeddings:
    supports_media = False
    max_batch = 2048

    def client(self, handler):
        client = OpenAIEmbeddingClient(model="conformance-embed", api_key="k")
        client.client = AsyncOpenAI(api_key="k", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return client

    def texts_in(self, request):
        body = json.loads(request.content)
        return body["input"] if isinstance(body["input"], list) else [body["input"]]

    def vectors_response(self, vectors):
        return httpx.Response(200, json={
            "object": "list", "model": "conformance-embed",
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    def error_response(self, status, message):
        return httpx.Response(status, json={"error": {"message": message, "type": "error", "code": None}})


class TestOpenAIEmbeddingClient(EmbeddingModelConformance):
    @pytest.fixture
    def provider(self):
        return OpenAIEmbeddings()


class _StubModel:
    """Stands in for the loaded sentence-transformers model — the 'transport' of a local embedder."""

    def __init__(self) -> None:
        self.fail = False
        self.seen: list[list[str]] = []

    def encode(self, texts, **_):
        if self.fail:
            raise RuntimeError("model failure")
        self.seen.append(list(texts))
        return np.array([vector_of(t) for t in texts], dtype=float).reshape(len(texts), 4)


class LocalModel:
    supports_media = False
    max_batch = None

    def client(self, handler):
        # No HTTP: the local model *is* the transport. The handler protocol is bridged by the stub.
        client = object.__new__(SentenceTransformersEmbeddingClient)
        model = _StubModel()
        client._model, client._batch_size, client._device = model, 64, "cpu"
        self.model = model
        probe = httpx.Request("POST", "http://local.invalid/embed")

        class Bridge(_StubModel):
            def encode(inner, texts, **kw):  # noqa: N805
                response = handler(httpx.Request("POST", "http://local.invalid/embed", json={"texts": list(texts)}))
                if response.status_code >= 400:
                    raise RuntimeError("model failure")
                return np.array(json.loads(response.content)["vectors"], dtype=float).reshape(len(texts), 4)

        client._model = Bridge()
        del probe
        return client

    def texts_in(self, request):
        return json.loads(request.content)["texts"]

    def vectors_response(self, vectors):
        return httpx.Response(200, json={"vectors": vectors})

    def error_response(self, status, message):
        return httpx.Response(status, json={"error": message})


class TestSentenceTransformersEmbeddingClient(EmbeddingModelConformance):
    @pytest.fixture
    def provider(self):
        return LocalModel()


# ---------------------------------------------------------------------------------------- Gemini

from google import genai  # noqa: E402
from google.genai import types as genai_types  # noqa: E402

from substrate.integrations.llm.gemini.gemini_embedding_client import GeminiEmbeddingClient  # noqa: E402


class GeminiEmbeddings:
    supports_media = False
    max_batch = 100

    def client(self, handler):
        client = GeminiEmbeddingClient(model="conformance-embed", api_key="k")
        client.client = genai.Client(
            api_key="k",
            http_options=genai_types.HttpOptions(
                httpx_client=_SyncBridge(handler), httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            ),
        )
        return client

    def texts_in(self, request):
        body = json.loads(request.content)
        requests = body.get("requests") or [body]
        return ["".join(p.get("text", "") for p in r["content"]["parts"]) for r in requests]

    def vectors_response(self, vectors):
        return httpx.Response(200, json={"embeddings": [{"values": v} for v in vectors]})

    def error_response(self, status, message):
        return httpx.Response(status, json={"error": {"code": status, "message": message, "status": "UNKNOWN"}})


def _SyncBridge(handler):  # noqa: N802
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestGeminiEmbeddingClient(EmbeddingModelConformance):
    @pytest.fixture
    def provider(self):
        return GeminiEmbeddings()


# ---------------------------------------------------------------------- embedding-reranker service

from substrate.integrations.services.embedding_reranker import (  # noqa: E402
    EmbeddingRerankerClient,
    EmbeddingRerankerTextEmbeddingClient,
)


class RerankerService:
    supports_media = True
    max_batch = None

    def client(self, handler):
        inner = EmbeddingRerankerClient(base_url="http://reranker.invalid")
        inner._client = httpx.AsyncClient(base_url="http://reranker.invalid", transport=httpx.MockTransport(handler))
        return EmbeddingRerankerTextEmbeddingClient(inner)

    def texts_in(self, request):
        body = json.loads(request.content)
        return [body["text"]] if "text" in body else [""]

    def vectors_response(self, vectors):
        return httpx.Response(200, json={"embedding": vectors[0]})

    def error_response(self, status, message):
        return httpx.Response(status, json={"detail": message})


class TestEmbeddingRerankerTextEmbeddingClient(EmbeddingModelConformance):
    @pytest.fixture
    def provider(self):
        return RerankerService()
