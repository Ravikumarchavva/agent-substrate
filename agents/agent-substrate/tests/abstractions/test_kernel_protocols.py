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
from substrate.models import EmbeddingModel, EmbeddingResult, Modality


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
    model = "fake"
    dimensions = 1
    max_input_tokens = 512
    modalities = frozenset({Modality.TEXT, Modality.IMAGE})

    def __init__(self) -> None:
        self.inputs: list = []

    async def embed(self, inputs, *, query: bool = False) -> EmbeddingResult:
        self.inputs.extend(inputs)
        return EmbeddingResult(
            embeddings=[[float(i)] for i, _ in enumerate(inputs)], model="fake"
        )


async def test_embed_accepts_a_string_and_blocks_embedded_together_as_one_vector() -> (
    None
):
    client = _RecordingEmbedder()
    assert isinstance(client, EmbeddingModel)

    result = await client.embed(
        [
            "a cat",
            [TextBlock(text="a cat"), MediaBlock(type="image", url="http://x/cat.png")],
        ]
    )

    assert len(result.embeddings) == 2 and client.inputs[0] == "a cat"


async def test_text_only_embedding_clients_reject_media_content() -> None:
    from substrate.integrations.llm.base import BaseEmbeddingClient

    class _TextOnlyClient(BaseEmbeddingClient):
        async def _embed_texts(
            self, texts: list[str], *, query: bool
        ) -> EmbeddingResult:
            return EmbeddingResult(embeddings=[[0.0] for _ in texts], model="fake")

    client = _TextOnlyClient(model="fake")
    assert isinstance(client, EmbeddingModel) and client.modalities == frozenset(
        {Modality.TEXT}
    )
    with pytest.raises(UnsupportedContentError):
        await client.embed(
            [[TextBlock(text="hello"), MediaBlock(type="image", data=b"abc")]]
        )
    assert (
        await client.embed(["a", [TextBlock(text="b"), TextBlock(text="c")]])
    ).embeddings == [[0.0], [0.0]]
