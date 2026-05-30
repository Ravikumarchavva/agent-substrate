from __future__ import annotations

import logging
import time
from typing import Any

from ravi.logger import setup_logging
from ravi.reasoning.middleware._contracts import MiddlewareContext

logger = setup_logging()


class AuditLoggerMiddleware:
    """Logs pre/post execution context for auditing."""

    def __init__(self, *, log_level: int = logging.DEBUG) -> None:
        self.log_level = log_level

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        ctx.metadata["_audit_t0"] = time.monotonic()
        logger.log(
            self.log_level,
            "[audit] %s START agent=%r tool=%s input_len=%d",
            ctx.stage.value,
            ctx.agent_name,
            ctx.tool_name,
            len(ctx.input_text),
        )
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        t0 = ctx.metadata.get("_audit_t0", time.monotonic())
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.log(
            self.log_level,
            "[audit] %s END agent=%r tool=%s elapsed=%.1fms",
            ctx.stage.value,
            ctx.agent_name,
            ctx.tool_name,
            elapsed_ms,
        )
        return result

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> None:
        t0 = ctx.metadata.get("_audit_t0", time.monotonic())
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.log(
            self.log_level,
            "[audit] %s ERROR agent=%r tool=%s elapsed=%.1fms error=%s: %s",
            ctx.stage.value,
            ctx.agent_name,
            ctx.tool_name,
            elapsed_ms,
            type(error).__name__,
            error,
        )
