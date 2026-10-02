"""Kernel protocol soundness, multimodal embedding, graph namespacing and
branch-isolated tasks — the contracts upgraded together."""

from __future__ import annotations

import importlib
import inspect
from typing import Protocol

import pytest

from tests._layout import contract_files, module_name
from substrate.types import MediaBlock, TextBlock
from substrate.types import UnsupportedContentError
from substrate.models import EmbeddingModel, EmbeddingResult


def _kernel_protocols() -> list[type]:
    found: dict[str, type] = {}
    for path in contract_files():
        mod = importlib.import_module(module_name(path))
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                obj.__module__ == mod.__name__
                and obj is not Protocol
                and Protocol in obj.__bases__
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return list(found.values())


PROTOCOLS = _kernel_protocols()


def test_protocol_discovery_is_not_vacuous() -> None:
    assert len(PROTOCOLS) >= 25  # guards against the walker silently finding nothing


@pytest.mark.parametrize("proto", PROTOCOLS, ids=lambda p: p.__name__)
def test_every_kernel_protocol_is_runtime_checkable(proto: type) -> None:
    # A non-runtime_checkable Protocol raises TypeError here.
    isinstance(object(), proto)


# ── Multimodal embedding ────────────────────────────────────────────────────


class _RecordingEmbedder:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(embeddings=[[0.0] for _ in texts], model="fake")

    async def embed_single(self, text: str) -> list[float]:
        self.texts.append(text)
        return [float(len(text))]

    async def embed_blocks(self, blocks) -> list[float]:
        from substrate.types import content_blocks_to_str

        return await self.embed_single(content_blocks_to_str(blocks))


async def test_embed_blocks_accepts_text_and_media() -> None:
    client = _RecordingEmbedder()
    assert isinstance(client, EmbeddingModel)

    vec = await client.embed_blocks(
        [TextBlock(text="a cat"), MediaBlock(type="image", url="http://x/cat.png")]
    )

    assert vec and "a cat" in client.texts[0]


async def test_sentence_transformers_and_base_clients_expose_embed_blocks() -> None:
    from substrate.integrations.llm.local_embeddings import (
        SentenceTransformersEmbeddingClient,
    )
    from substrate.integrations.llm.base import BaseEmbeddingClient

    assert hasattr(SentenceTransformersEmbeddingClient, "embed_blocks")
    assert hasattr(BaseEmbeddingClient, "embed_blocks")


async def test_text_only_embedding_clients_reject_media_content() -> None:
    from substrate.integrations.llm.local_embeddings import (
        SentenceTransformersEmbeddingClient,
    )
    from substrate.integrations.llm.base import BaseEmbeddingClient

    class _TextOnlyClient(BaseEmbeddingClient):
        async def embed(self, texts: list[str]) -> EmbeddingResult:
            return EmbeddingResult(embeddings=[[0.0] for _ in texts], model="fake")

    with pytest.raises(UnsupportedContentError):
        await _TextOnlyClient(model="fake").embed_blocks(
            [TextBlock(text="hello"), MediaBlock(type="image", data=b"abc")]
        )

    with pytest.raises(UnsupportedContentError):
        await SentenceTransformersEmbeddingClient(
            model="sentence-transformers/all-MiniLM-L6-v2"
        ).embed_blocks([MediaBlock(type="image", data=b"abc")])


# ── Branch-isolated tasks ───────────────────────────────────────────────────


# ── Graph namespacing ───────────────────────────────────────────────────────
