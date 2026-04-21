"""Semantic response cache — avoid redundant LLM calls for similar queries.

Embeds the query, searches for similar cached queries (cosine > threshold),
and returns the cached response on hit.  Stores in Redis with TTL.

Usage::

    from ravi.core.llm.cache import SemanticCache

    cache = SemanticCache(
        embedding_client=embed_client,
        redis_url="redis://localhost:6379/0",
    )
    await cache.connect()

    # Check cache before calling LLM
    hit = await cache.get(query_text)
    if hit:
        return hit  # cached response string

    # On miss — call LLM, then cache
    response = await model_client.generate(messages)
    await cache.put(query_text, response_text)
"""

from __future__ import annotations

import hashlib
import logging
import struct
from typing import TYPE_CHECKING, Optional

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from ravi.core.llm.base_embedding_client import BaseEmbeddingClient

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "semcache:"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _pack_embedding(embedding: list[float]) -> bytes:
    """Pack a float list into compact bytes."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack bytes back into a float list."""
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


class SemanticCache:
    """Embedding-based semantic cache backed by Redis.

    Each cache entry stores:
    - The query embedding (as packed bytes)
    - The response text
    - TTL for automatic expiry

    On lookup, the query is embedded and compared against all cached
    embeddings for similarity.  This is a brute-force approach suitable
    for moderate cache sizes (< 10k entries).  For larger caches, use
    pgvector instead.
    """

    def __init__(
        self,
        embedding_client: BaseEmbeddingClient,
        redis_url: str = "redis://localhost:6379/0",
        threshold: float = 0.95,
        ttl: int = 3600,
        max_entries: int = 5000,
        namespace: str = "default",
    ) -> None:
        self._embedding = embedding_client
        self._redis_url = redis_url
        self._threshold = threshold
        self._ttl = ttl
        self._max_entries = max_entries
        self._namespace = namespace
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        """Connect to Redis."""
        self._redis = aioredis.from_url(self._redis_url, decode_responses=False)

    async def disconnect(self) -> None:
        """Close the Redis connection."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    def _key_prefix(self) -> str:
        return f"{_CACHE_PREFIX}{self._namespace}:"

    async def get(self, query: str) -> Optional[str]:
        """Look up a cached response by semantic similarity.

        Returns the cached response string, or ``None`` on miss.
        """
        if not self._redis:
            return None

        query_embedding = await self._embedding.embed_single(query)

        # Scan all cache entries in this namespace
        prefix = self._key_prefix()
        best_score = 0.0
        best_response: Optional[str] = None

        async for key in self._redis.scan_iter(match=f"{prefix}*", count=100):
            data = await self._redis.hgetall(key)  # type: ignore[arg-type]
            if not data:
                continue

            cached_emb_bytes = data.get(b"embedding")
            cached_response = data.get(b"response")
            if not cached_emb_bytes or not cached_response:
                continue

            cached_embedding = _unpack_embedding(cached_emb_bytes)
            score = _cosine_similarity(query_embedding, cached_embedding)

            if score > best_score:
                best_score = score
                best_response = cached_response.decode("utf-8")

        if best_score >= self._threshold and best_response is not None:
            logger.debug(
                "Semantic cache HIT (score=%.4f, threshold=%.4f)",
                best_score,
                self._threshold,
            )
            return best_response

        logger.debug(
            "Semantic cache MISS (best_score=%.4f, threshold=%.4f)",
            best_score,
            self._threshold,
        )
        return None

    async def put(self, query: str, response: str) -> None:
        """Store a query-response pair in the cache."""
        if not self._redis:
            return

        query_embedding = await self._embedding.embed_single(query)

        # Use a hash of the query as the key
        query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
        key = f"{self._key_prefix()}{query_hash}"

        await self._redis.hset(  # type: ignore[misc]
            key,
            mapping={
                "embedding": _pack_embedding(query_embedding),
                "response": response.encode("utf-8"),
                "query": query[:500].encode("utf-8"),  # truncated for debugging
            },
        )
        await self._redis.expire(key, self._ttl)

    async def clear(self) -> int:
        """Clear all entries in this namespace.

        Returns the number of entries deleted.
        """
        if not self._redis:
            return 0

        prefix = self._key_prefix()
        count = 0
        async for key in self._redis.scan_iter(match=f"{prefix}*", count=100):
            await self._redis.delete(key)
            count += 1
        return count
