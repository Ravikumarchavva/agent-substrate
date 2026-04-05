"""Cache middleware.

Caches tool results by input hash.  Cache hits skip tool execution
entirely, saving time and API costs for deterministic tools.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict

from raavan.core.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)

logger = logging.getLogger(__name__)


class CacheMiddleware(BaseMiddleware):
    """In-memory cache for tool results.

    Keyed on ``(tool_name, sorted(tool_args))``.  Only caches during
    ``TOOL_EXECUTION`` stage.  Use ``max_entries`` to cap memory usage.
    """

    def __init__(
        self,
        *,
        name: str = "cache",
        max_entries: int = 256,
    ) -> None:
        super().__init__(name)
        self.max_entries = max_entries
        self._cache: Dict[str, Any] = {}

    def _make_key(self, ctx: MiddlewareContext) -> str:
        raw = json.dumps(
            {"tool": ctx.tool_name, "args": ctx.tool_args},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        if ctx.stage != MiddlewareStage.TOOL_EXECUTION:
            return ctx

        key = self._make_key(ctx)
        if key in self._cache:
            logger.debug(f"CacheMiddleware: hit for {ctx.tool_name}")
            ctx.metadata["_cache_hit"] = True
            ctx.metadata["_cache_result"] = self._cache[key]
        else:
            ctx.metadata["_cache_hit"] = False

        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        if ctx.stage != MiddlewareStage.TOOL_EXECUTION:
            return result

        if ctx.metadata.get("_cache_hit"):
            return ctx.metadata["_cache_result"]

        # Store in cache
        key = self._make_key(ctx)
        if len(self._cache) >= self.max_entries:
            # Evict oldest entry (FIFO)
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = result

        return result

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()
