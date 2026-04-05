"""Schema validation middleware.

Validates the LLM's output against a Pydantic model.  If validation
fails, it can optionally retry (by re-raising to trigger the
``RetryMiddleware`` or by flagging the result for upstream handling).
"""

from __future__ import annotations

import logging
from typing import Any

from raavan.core.middleware.base import BaseMiddleware, MiddlewareContext

logger = logging.getLogger(__name__)


class SchemaValidatorMiddleware(BaseMiddleware):
    """Validates LLM output against ``ctx.response_schema``.

    Placed in ``after()`` — checks whether the result text conforms to the
    expected Pydantic schema and attaches ``ctx.metadata["schema_valid"]``.
    """

    def __init__(self, *, name: str = "schema_validator") -> None:
        super().__init__(name)

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        schema = ctx.response_schema
        if schema is None:
            return result

        # Try to validate from the parsed field on AssistantMessage
        parsed = getattr(result, "parsed", None)
        if parsed is not None:
            ctx.metadata["schema_valid"] = True
            return result

        # Try to validate from the text content
        content = getattr(result, "content", None)
        if content and isinstance(content, list) and len(content) > 0:
            text = content[0] if isinstance(content[0], str) else ""
            if text:
                try:
                    obj = schema.model_validate_json(text)
                    result.parsed = obj
                    ctx.metadata["schema_valid"] = True
                    return result
                except Exception as exc:
                    logger.warning(f"SchemaValidator: validation failed: {exc}")
                    ctx.metadata["schema_valid"] = False

        return result
