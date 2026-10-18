"""An embedder and a reranker reached by URL — standard library only.

``RemoteEmbedder(url)`` speaks the OpenAI embeddings wire (``POST {url}/v1/embeddings``), so it works with ``apps/embedding-reranker`` (the
default: Qwen3-VL embedding and reranking, text and images in one space) and equally with llama.cpp, vLLM, Ollama and TEI, which serve the
same endpoint. ``RemoteReranker(url)`` speaks the Jina/Cohere rerank wire (``POST {url}/v1/rerank``), which the same servers share.

Beyond the OpenAI shape an input may be an image or a mixed text-and-image item, for a server that embeds them; a server that does not
answers 4xx, and that is raised, never swallowed:

* a string                       → ``"text"``
* ``[TextBlock, …]``             → the blocks' text joined
* an image block                 → ``{"image": "<base64>"}``
* text and images together       → ``{"content": ["text", {"image": "<base64>"}, …]}`` — one vector for the whole item

``query=True`` is sent as ``"input_type": "query"`` for a server that embeds a query differently (Qwen3 prepends an instruction); a
server that does not know the field ignores it. What the model is (``dimensions``, ``max_input_tokens``, ``modalities``) is read from
``GET {url}/v1/models`` the first time it is needed; a server that does not serve it leaves the defaults, and ``dimensions`` is learned
from the first vector.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from typing import Any

from substrate.models.http import join_url, request_json
from substrate.models.protocols import EmbeddingResult, Modality
from substrate.types.content import ContentBlock, MediaBlock, TextBlock
from substrate.types.errors import PermanentError, UnsupportedContentError

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 64


def _wire_item(
    item: str | Sequence[ContentBlock], *, images: bool
) -> str | dict[str, Any]:
    if isinstance(item, str):
        return item
    parts: list[str | dict[str, str]] = []
    for block in item:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif (
            isinstance(block, MediaBlock)
            and block.type == "image"
            and block.data is not None
        ):
            if not images:
                raise UnsupportedContentError(
                    "this embedder takes text only; an image block cannot be embedded"
                )
            parts.append({"image": base64.b64encode(block.data).decode("ascii")})
        else:
            raise UnsupportedContentError(
                f"cannot embed a {type(block).__name__}{' of type ' + block.type if isinstance(block, MediaBlock) else ''}"
            )
    if all(isinstance(p, str) for p in parts):
        return "".join(p for p in parts if isinstance(p, str))
    if len(parts) == 1:
        return parts[0]  # type: ignore[return-value]
    return {"content": parts}


class RemoteEmbedder:
    def __init__(
        self,
        url: str,
        *,
        model: str = "",
        api_key: str = "",
        dimensions: int | None = None,
        max_input_tokens: int = 512,
        modalities: frozenset[Modality] | None = None,
        batch_size: int = DEFAULT_BATCH,
        timeout: float = 60.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.max_input_tokens = max_input_tokens
        self.modalities = modalities or frozenset({Modality.TEXT})
        self._api_key = api_key
        self._batch = max(1, batch_size)
        self._timeout = timeout
        self._described = False

    async def describe(self) -> None:
        """Read the model's facts from ``/v1/models``; a server without it keeps what was passed in."""
        self._described = True
        try:
            answer = await request_json(
                "GET",
                join_url(self.url, "/v1/models"),
                api_key=self._api_key,
                timeout=10.0,
            )
            entry = (answer.get("data") or [{}])[0] if isinstance(answer, dict) else {}
        except Exception as exc:  # noqa: BLE001 — optional: an embedder without /v1/models is still an embedder
            logger.debug("%s has no /v1/models: %s", self.url, exc)
            return
        if not isinstance(entry, dict):
            return
        self.model = self.model or str(entry.get("id") or "")
        if self.dimensions is None and isinstance(entry.get("dimensions"), int):
            self.dimensions = entry["dimensions"]
        if isinstance(entry.get("max_input_tokens"), int):
            self.max_input_tokens = entry["max_input_tokens"]
        if isinstance(entry.get("modalities"), list):
            self.modalities = frozenset(
                Modality(m)
                for m in entry["modalities"]
                if m in Modality._value2member_map_
            )

    async def embed(
        self, inputs: Sequence[str | Sequence[ContentBlock]], *, query: bool = False
    ) -> EmbeddingResult:
        if not self._described:
            await self.describe()
        images = Modality.IMAGE in self.modalities
        items = [
            _wire_item(item, images=images) for item in inputs
        ]  # raises before anything is sent
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(items), self._batch):
            body: dict[str, Any] = {
                "model": self.model,
                "input": items[start : start + self._batch],
            }
            if query:
                body["input_type"] = "query"
            answer = await request_json(
                "POST",
                join_url(self.url, "/v1/embeddings"),
                json_body=body,
                api_key=self._api_key,
                timeout=self._timeout,
            )
            rows = answer.get("data") if isinstance(answer, dict) else None
            expected = len(body["input"])
            if not isinstance(rows, list) or len(rows) != expected:
                raise PermanentError(
                    f"{self.url} answered {len(rows) if isinstance(rows, list) else 'no'} embeddings for {expected} inputs"
                )
            try:
                ordered = sorted(rows, key=lambda row: row.get("index", 0))
                vectors.extend([float(x) for x in row["embedding"]] for row in ordered)
            except (KeyError, TypeError, ValueError) as exc:
                raise PermanentError(
                    f"{self.url} answered embeddings in a shape this client does not read"
                ) from exc
            usage = answer.get("usage") if isinstance(answer, dict) else None
            tokens += (
                int(usage.get("total_tokens", 0)) if isinstance(usage, dict) else 0
            )
        if vectors and self.dimensions is None:
            self.dimensions = len(vectors[0])
        if len({len(v) for v in vectors}) > 1:
            raise PermanentError(f"{self.url} answered vectors of different widths")
        return EmbeddingResult(
            embeddings=vectors, model=self.model, usage_tokens=tokens
        )


class RemoteReranker:
    def __init__(
        self, url: str, *, model: str = "", api_key: str = "", timeout: float = 60.0
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout

    async def rerank(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        body = {
            "model": self.model,
            "query": query,
            "documents": list(passages),
            "top_n": len(passages),
        }
        answer = await request_json(
            "POST",
            join_url(self.url, "/v1/rerank"),
            json_body=body,
            api_key=self._api_key,
            timeout=self._timeout,
        )
        rows = answer.get("results") if isinstance(answer, dict) else None
        if not isinstance(rows, list):
            raise PermanentError(f"{self.url} did not answer rerank scores")
        scores: list[float | None] = [None] * len(passages)
        try:
            for row in rows:
                index = int(row["index"])
                if 0 <= index < len(scores):
                    scores[index] = float(row["relevance_score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PermanentError(
                f"{self.url} answered rerank scores in a shape this client does not read"
            ) from exc
        if any(score is None for score in scores):
            raise PermanentError(
                f"{self.url} scored {sum(s is not None for s in scores)} of {len(passages)} passages"
            )
        return [float(s) for s in scores if s is not None]


__all__ = ["RemoteEmbedder", "RemoteReranker"]
