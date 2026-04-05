"""File validation middleware.

Pre-checks that files referenced in tool arguments actually exist, have
the correct extension, and don't exceed a size limit.  This prevents
wasted LLM tokens on tool calls that would obviously fail.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Set

from raavan.core.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)

logger = logging.getLogger(__name__)


class FileValidatorMiddleware(BaseMiddleware):
    """Validates file paths in tool arguments before execution.

    Only runs during ``TOOL_EXECUTION`` stage.  Scans ``tool_args``
    for keys containing ``file`` or ``path`` and validates each.
    """

    def __init__(
        self,
        *,
        name: str = "file_validator",
        allowed_extensions: Optional[Set[str]] = None,
        max_file_size_bytes: int = 100 * 1024 * 1024,  # 100 MB
    ) -> None:
        super().__init__(name)
        self.allowed_extensions = allowed_extensions
        self.max_file_size_bytes = max_file_size_bytes

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        if ctx.stage != MiddlewareStage.TOOL_EXECUTION:
            return ctx
        if not ctx.tool_args:
            return ctx

        for key, value in ctx.tool_args.items():
            if not isinstance(value, str):
                continue
            if "file" not in key.lower() and "path" not in key.lower():
                continue

            p = Path(value)
            if not p.exists():
                raise FileNotFoundError(
                    f"FileValidator: {key}={value!r} does not exist"
                )
            if p.is_file():
                if self.allowed_extensions is not None:
                    ext = p.suffix.lower()
                    if ext not in self.allowed_extensions:
                        raise ValueError(
                            f"FileValidator: extension {ext!r} not in "
                            f"allowed set {self.allowed_extensions}"
                        )
                if p.stat().st_size > self.max_file_size_bytes:
                    raise ValueError(
                        f"FileValidator: {p.name} exceeds "
                        f"{self.max_file_size_bytes} byte limit"
                    )

        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return result
