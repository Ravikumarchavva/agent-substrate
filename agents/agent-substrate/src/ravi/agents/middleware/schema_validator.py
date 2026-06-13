from __future__ import annotations

from typing import Callable, Awaitable

from ravi.logger import setup_logging
from ravi.agents.middleware._contracts import ChatContext
from ravi.kernel.core.content import TextBlock

logger = setup_logging()


class SchemaValidatorMiddleware:
    """Validates LLM output against a Pydantic schema stored in context.metadata."""

    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        await call_next()

        schema = context.metadata.get("response_schema")
        if schema is None or context.result is None:
            return

        parsed = getattr(context.result, "parsed", None)
        if parsed is not None:
            context.metadata["schema_valid"] = True
            return

        content = getattr(context.result, "content", None)
        if content and isinstance(content, list) and len(content) > 0:
            block = content[0]
            text = block.text if isinstance(block, TextBlock) else ""
            if text:
                try:
                    obj = schema.model_validate_json(text)
                    context.result.parsed = obj
                    context.metadata["schema_valid"] = True
                except Exception as exc:
                    logger.warning("SchemaValidator: validation failed: %s", exc)
                    context.metadata["schema_valid"] = False
