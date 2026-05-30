from __future__ import annotations

import hashlib
import json
from typing import Any

from ravi.logger import setup_logging
from ravi.reasoning.middleware._contracts import MiddlewareContext, MiddlewareStage

logger = setup_logging()


class CacheMiddleware:
    """In-memory cache for tool results, keyed on (tool_name, sorted_args)."""

    def __init__(self, *, max_entries: int = 256) -> None:
        self.max_entries = max_entries
        self._cache: dict[str, Any] = {}

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
            logger.debug("CacheMiddleware: hit for %s", ctx.tool_name)
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
        key = self._make_key(ctx)
        if len(self._cache) >= self.max_entries:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = result
        return result

    def clear(self) -> None:
        self._cache.clear()
