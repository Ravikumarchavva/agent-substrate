"""Audit logging middleware.

Logs every middleware step for debugging and compliance.
Captures timing, inputs, outputs, and errors.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from raavan.core.middleware.base import BaseMiddleware, MiddlewareContext

logger = logging.getLogger(__name__)


class AuditLoggerMiddleware(BaseMiddleware):
    """Logs pre/post execution context for auditing.

    By default logs at DEBUG level.  Set ``log_level`` to ``logging.INFO``
    for production audit trails.
    """

    def __init__(
        self,
        *,
        name: str = "audit_logger",
        log_level: int = logging.DEBUG,
    ) -> None:
        super().__init__(name)
        self.log_level = log_level

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        ctx.metadata["_audit_t0"] = time.monotonic()
        logger.log(
            self.log_level,
            f"[audit] {ctx.stage.value} START agent={ctx.agent_name!r} "
            f"tool={ctx.tool_name} input_len={len(ctx.input_text)}",
        )
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        t0 = ctx.metadata.get("_audit_t0", time.monotonic())
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.log(
            self.log_level,
            f"[audit] {ctx.stage.value} END agent={ctx.agent_name!r} "
            f"tool={ctx.tool_name} elapsed={elapsed_ms:.1f}ms",
        )
        return result

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> Optional[Any]:
        t0 = ctx.metadata.get("_audit_t0", time.monotonic())
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.log(
            self.log_level,
            f"[audit] {ctx.stage.value} ERROR agent={ctx.agent_name!r} "
            f"tool={ctx.tool_name} elapsed={elapsed_ms:.1f}ms "
            f"error={type(error).__name__}: {error}",
        )
        return None
