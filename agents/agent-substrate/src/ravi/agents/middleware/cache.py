from __future__ import annotations

import hashlib
import json
from typing import Callable, Awaitable, Any

from ravi.logger import setup_logging
from ravi.agents.middleware._contracts import FunctionContext

logger = setup_logging()


class CacheMiddleware:
    """In-memory cache for tool results, keyed on (function_name, sorted_args)."""

    def __init__(self, *, max_entries: int = 256) -> None:
        self.max_entries = max_entries
        self._cache: dict[str, Any] = {}

    def _make_key(self, ctx: FunctionContext) -> str:
        raw = json.dumps(
            {"function": ctx.function_name, "args": ctx.arguments},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def process(self, context: FunctionContext, call_next: Callable[[], Awaitable[None]]) -> None:
        key = self._make_key(context)
        if key in self._cache:
            logger.debug("CacheMiddleware: hit for %s", context.function_name)
            context.metadata["_cache_hit"] = True
            context.result = self._cache[key]
            return  # Skip call_next() on cache hit
            
        context.metadata["_cache_hit"] = False
        await call_next()
        
        if len(self._cache) >= self.max_entries:
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = context.result

    def clear(self) -> None:
        self._cache.clear()
