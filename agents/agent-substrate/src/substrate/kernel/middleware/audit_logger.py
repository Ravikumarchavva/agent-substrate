from __future__ import annotations

import logging
import time
from typing import Callable, Awaitable, ClassVar

from substrate.logger import setup_logging
from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.kernel.agent.middleware import MiddlewareStage

logger = setup_logging()


class AuditLoggerMiddleware:
    """Logs pre/post execution context for auditing."""

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TURN})

    def __init__(self, *, log_level: int = logging.DEBUG) -> None:
        self.log_level = log_level

    async def process(
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        t0 = time.monotonic()
        logger.log(
            self.log_level,
            "[audit] RUN START agent=%r run_id=%s session_id=%s msgs=%d",
            context.agent_name,
            context.run_id,
            context.session_id,
            len(context.messages or []),
        )

        try:
            await call_next()
            elapsed_ms = (time.monotonic() - t0) * 1000
            status = context.turn_result.status if context.turn_result else "error"
            logger.log(
                self.log_level,
                "[audit] RUN END agent=%r run_id=%s elapsed=%.1fms status=%s",
                context.agent_name,
                context.run_id,
                elapsed_ms,
                status,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.log(
                self.log_level,
                "[audit] RUN ERROR agent=%r run_id=%s elapsed=%.1fms error=%s: %s",
                context.agent_name,
                context.run_id,
                elapsed_ms,
                type(exc).__name__,
                exc,
            )
            raise
