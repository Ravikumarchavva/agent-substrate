"""Conformance suite for ``EmbeddingModel``.

Like the LLM-client suite, a client is driven through a scripted transport so the vendor's real SDK or
HTTP code runs. A ``Provider`` says how to build the client around a handler and how that vendor's
request carries its texts and its response carries vectors; the suite supplies the vector for each text
(``vector_of``) so *order* and *pairing* are checked, not just shape.

What every client must do: return exactly one vector per input, in the input's order, all the same
width; treat one text, a batch and an empty list consistently; refuse media it cannot embed rather than
drop it; accept ``query=True``; say what model it is (``model``, ``modalities``, ``max_input_tokens``,
``dimensions``); survive hostile text; and raise — never return wrong vectors — when the provider fails.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import pytest

from substrate.types.content import MediaBlock, TextBlock
from substrate.types.errors import UnsupportedContentError
from substrate.models.protocols import EmbeddingModel, Modality

WIDTH = 4


def vector_of(text: str) -> list[float]:
    """A deterministic, text-specific vector: different texts get different vectors."""
    return [float(len(text)), float(sum(map(ord, text)) % 997), float(text.count(" ")), 1.0]


Handler = Callable[[httpx.Request], httpx.Response]


class Provider(Protocol):
    supports_media: bool
    max_batch: int | None
    """The most texts the vendor accepts in one request; the client must split larger batches."""

    def client(self, handler: Handler) -> EmbeddingModel: ...

    def texts_in(self, request: httpx.Request) -> list[str]:
        """The texts the vendor's request body carries, in order."""

    def vectors_response(self, vectors: list[list[float]]) -> httpx.Response: ...

    def error_response(self, status: int, message: str) -> httpx.Response: ...


@dataclass
class Calls:
    requests: list[httpx.Request] = field(default_factory=list)
    texts: list[list[str]] = field(default_factory=list)


class EmbeddingModelConformance:
    @pytest.fixture
    def provider(self) -> Provider:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    def build(self, provider: Provider, *, fail_with: int | None = None) -> tuple[EmbeddingModel, Calls]:
        calls = Calls()

        def handler(request: httpx.Request) -> httpx.Response:
            if fail_with is not None:
                return provider.error_response(fail_with, "provider failure")
            texts = provider.texts_in(request)
            if provider.max_batch is not None and len(texts) > provider.max_batch:
                return provider.error_response(400, f"at most {provider.max_batch} inputs per request")
            calls.requests.append(request)
            calls.texts.append(texts)
            return provider.vectors_response([vector_of(t) for t in texts])

        return provider.client(handler), calls

    async def test_each_text_gets_its_own_vector_in_input_order(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        texts = ["alpha", "a much longer second text", "γ", "with  double  spaces"]
        result = await client.embed(texts)
        assert result.embeddings == [vector_of(t) for t in texts], "vectors came back reordered, dropped or mis-paired"

    async def test_all_vectors_have_the_same_width(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        result = await client.embed(["a", "bb", "ccc"])
        assert {len(v) for v in result.embeddings} == {WIDTH}

    async def test_the_result_names_the_model(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        assert isinstance((await client.embed(["a"])).model, str)

    async def test_it_says_what_model_it_is(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        assert isinstance(client.model, str) and client.max_input_tokens > 0 and Modality.TEXT in client.modalities
        assert client.dimensions is None or client.dimensions > 0
        await client.embed(["a"])
        assert client.dimensions in (None, WIDTH)  # learned from the first vector if it was not known

    async def test_a_query_is_embedded_like_any_text(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        result = await client.embed(["what is the refund policy"], query=True)
        assert len(result.embeddings) == 1 and len(result.embeddings[0]) == WIDTH

    async def test_a_large_batch_returns_every_vector_whatever_the_providers_batch_limit(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        texts = [f"document number {i}" for i in range(330)]
        result = await client.embed(texts)
        assert result.embeddings == [vector_of(t) for t in texts]

    async def test_embedding_nothing_returns_nothing(self, provider: Provider) -> None:
        client, _ = self.build(provider)
        assert (await client.embed([])).embeddings == []

    async def test_text_blocks_are_embedded_as_their_joined_text(self, provider: Provider) -> None:
        client, calls = self.build(provider)
        result = await client.embed([[TextBlock(text="hello "), TextBlock(text="world")]])
        assert result.embeddings == [vector_of("hello world")]

    async def test_media_a_text_only_client_cannot_embed_is_refused_not_silently_dropped(self, provider: Provider) -> None:
        if provider.supports_media:
            pytest.skip("this client embeds media")
        client, calls = self.build(provider)
        with pytest.raises(UnsupportedContentError):
            await client.embed([[TextBlock(text="caption"), MediaBlock.image(data=b"\x89PNG....", media_type="image/png")]])
        assert calls.requests == [], "the request was sent with the image silently dropped"

    @pytest.mark.parametrize("hostile", ["", " ", "\n\n", "x' OR '1'='1", "a" * 20_000, "ünï-çødé 日本語 🙂", "\x00\x01 control"])
    async def test_hostile_text_is_data_not_a_failure(self, provider: Provider, hostile: str) -> None:
        client, _ = self.build(provider)
        try:
            result = await client.embed([hostile, "after"])
        except UnsupportedContentError:
            return  # refusing a text it cannot encode is allowed; crashing or mis-pairing is not
        assert result.embeddings[-1] == vector_of("after"), "a hostile text shifted the vectors of the texts after it"

    async def test_a_provider_failure_raises_instead_of_returning_wrong_vectors(self, provider: Provider) -> None:
        client, _ = self.build(provider, fail_with=500)
        with pytest.raises(Exception):
            await client.embed(["a"])


__all__ = ["EmbeddingModelConformance", "Provider", "vector_of", "WIDTH", "Any"]
