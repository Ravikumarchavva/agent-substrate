from __future__ import annotations

import logging

from ravi.kernel import Message

logger = logging.getLogger(__name__)


class AuditLoggerMiddleware:
    """Logs every message to satisfy compliance requirements."""

    async def pre_process(self, message: Message) -> Message:
        logger.info("AUDIT in:  %s → %s", message.sender, message.target)
        return message

    async def post_process(self, message: Message) -> Message:
        logger.info("AUDIT out: %s → %s", message.sender, message.target)
        return message
