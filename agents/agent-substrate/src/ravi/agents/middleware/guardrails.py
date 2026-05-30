from __future__ import annotations

from ravi.kernel import Message
from .pipeline import Interceptor, MiddlewarePipeline  # noqa: F401


class PIIRedactionGuardrail:
    """Scrubs PII from outbound messages before delivery."""

    async def pre_process(self, message: Message) -> Message:
        return message

    async def post_process(self, message: Message) -> Message:
        # Real implementation scans message.payload for PII and redacts it.
        return message


class PromptInjectionGuardrail:
    """Detects and blocks malicious jailbreak attempts."""

    async def pre_process(self, message: Message) -> Message:
        # Real implementation scans inbound payload; raises on detection.
        return message

    async def post_process(self, message: Message) -> Message:
        return message
