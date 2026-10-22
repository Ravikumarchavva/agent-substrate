"""`RemoteEmbedder`, held to the embedding-model conformance suite against a real HTTP server."""

from __future__ import annotations

import json
from contextlib import ExitStack

import httpx
import pytest

from loamz.engine.models.remote import RemoteEmbedder
from loamz.engine.testing.conformance.embedding_model import EmbeddingModelConformance
from tests.storage._httpserve import serve


class RemoteService:
    """``RemoteEmbedder`` against a real HTTP server speaking the OpenAI embeddings wire."""

    supports_media = False
    max_batch = 64

    def __init__(self) -> None:
        self._stack = []

    def client(self, handler):
        stack = ExitStack()
        self._stack.append(stack)
        url = stack.enter_context(serve(handler))
        return RemoteEmbedder(url, model="conformance-embed")

    def texts_in(self, request):
        body = json.loads(request.content)
        return body["input"]

    def vectors_response(self, vectors):
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": i, "embedding": v}
                    for i, v in enumerate(vectors)
                ],
            },
        )

    def error_response(self, status, message):
        return httpx.Response(status, json={"error": message})


class TestRemoteEmbedder(EmbeddingModelConformance):
    @pytest.fixture
    def provider(self):
        provider = RemoteService()
        yield provider
        for stack in provider._stack:
            stack.close()
