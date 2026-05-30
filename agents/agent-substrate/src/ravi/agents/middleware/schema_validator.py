from __future__ import annotations

from typing import Any

from ravi.logger import setup_logging
from ravi.agents.middleware._contracts import MiddlewareContext

logger = setup_logging()


class SchemaValidatorMiddleware:
    """Validates LLM output against a Pydantic schema stored in ctx.response_schema."""

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        schema = ctx.response_schema
        if schema is None:
            return result
        parsed = getattr(result, "parsed", None)
        if parsed is not None:
            ctx.metadata["schema_valid"] = True
            return result
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
                    logger.warning("SchemaValidator: validation failed: %s", exc)
                    ctx.metadata["schema_valid"] = False
        return result
