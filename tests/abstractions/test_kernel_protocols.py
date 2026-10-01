"""Kernel protocol soundness, multimodal embedding, graph namespacing and
branch-isolated tasks — the contracts upgraded together."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Protocol

import pytest

import substrate.kernel as kernel_pkg
from substrate.agents.storage.graph import InMemoryGraphStore
from substrate.agents.storage.tasks import TaskStore as InMemoryTaskStore
from substrate.kernel.core.content import MediaBlock, TextBlock
from substrate.kernel.exceptions import UnsupportedContentError
from substrate.kernel.llm.llm import EmbeddingClient, EmbeddingResult
from substrate.kernel.storage.graph import Entity, GraphStore, Relationship
from substrate.kernel.storage.tasks import TaskStatus, TaskStore


def _kernel_protocols() -> list[type]:
    found: dict[str, type] = {}
    for mod_info in pkgutil.walk_packages(kernel_pkg.__path__, "substrate.kernel."):
        mod = importlib.import_module(mod_info.name)
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
    assert len(PROTOCOLS) >= 30  # guards against the walker silently finding nothing


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
        from substrate.kernel.core.content import content_blocks_to_str

        return await self.embed_single(content_blocks_to_str(blocks))


async def test_embed_blocks_accepts_text_and_media() -> None:
    client = _RecordingEmbedder()
    assert isinstance(client, EmbeddingClient)

    vec = await client.embed_blocks(
        [TextBlock(text="a cat"), MediaBlock(type="image", url="http://x/cat.png")]
    )

    assert vec and "a cat" in client.texts[0]


async def test_sentence_transformers_and_base_clients_expose_embed_blocks() -> None:
    from substrate.agents.llm.embedding_client import (
        SentenceTransformersEmbeddingClient,
    )
    from substrate.integrations.llm.base import BaseEmbeddingClient

    assert hasattr(SentenceTransformersEmbeddingClient, "embed_blocks")
    assert hasattr(BaseEmbeddingClient, "embed_blocks")


async def test_text_only_embedding_clients_reject_media_content() -> None:
    from substrate.agents.llm.embedding_client import (
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
        await SentenceTransformersEmbeddingClient(model="sentence-transformers/all-MiniLM-L6-v2").embed_blocks(
            [MediaBlock(type="image", data=b"abc")]
        )


# ── Branch-isolated tasks ───────────────────────────────────────────────────


async def test_task_branches_do_not_touch_main() -> None:
    store = InMemoryTaskStore()
    assert isinstance(store, TaskStore)

    main = await store.create_task_list("conv", ["ship it"])
    exp = await store.create_task_list("conv", ["try risky idea"], branch_id="experiment")
    await store.update_status(exp.id, exp.tasks[0].id, TaskStatus.FAILED)

    assert main.branch_id == "main" and exp.branch_id == "experiment"
    got_main = await store.get_by_conversation("conv")
    got_exp = await store.get_by_conversation("conv", "experiment")
    assert got_main is not None and got_main.id == main.id
    assert got_main.tasks[0].status == TaskStatus.PLANNED
    assert got_exp is not None and got_exp.tasks[0].status == TaskStatus.FAILED
    assert [b.id for b in await store.get_boards_by_conversation("conv")] == [main.id]


# ── Graph namespacing ───────────────────────────────────────────────────────


async def test_graph_namespaces_do_not_leak() -> None:
    store = InMemoryGraphStore()
    assert isinstance(store, GraphStore)

    a, b = Entity(label="P", id="a"), Entity(label="P", id="b")
    await store.add_entities([a, b], namespace="tenant_a")
    await store.add_relationships(
        [Relationship(source_id="a", target_id="b", type="KNOWS")],
        namespace="tenant_a",
    )

    assert (await store.get_neighbors("a", namespace="tenant_a")).entities
    assert not (await store.get_neighbors("a", namespace="tenant_b")).entities
    assert (await store.get_neighbors("a")).entities  # "" sees the whole graph
    assert await store.delete_entity("a", namespace="tenant_b") is False
    assert await store.delete_entity("a", namespace="tenant_a") is True
