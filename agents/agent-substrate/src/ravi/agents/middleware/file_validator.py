from __future__ import annotations

from pathlib import Path
from typing import Callable, Awaitable

from ravi.logger import setup_logging
from ravi.agents.middleware._contracts import FunctionContext
from ravi.exceptions import MiddlewareTermination

logger = setup_logging()


class FileValidatorMiddleware:
    """Validates file paths in tool arguments before execution."""

    def __init__(
        self,
        *,
        allowed_extensions: set[str] | None = None,
        max_file_size_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.allowed_extensions = allowed_extensions
        self.max_file_size_bytes = max_file_size_bytes

    async def process(
        self, context: FunctionContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        if not context.arguments:
            await call_next()
            return

        for key, value in context.arguments.items():
            if not isinstance(value, str):
                continue
            if "file" not in key.lower() and "path" not in key.lower():
                continue

            p = Path(value)
            if not p.exists():
                raise MiddlewareTermination(
                    f"FileValidator: {key}={value!r} does not exist"
                )

            if p.is_file():
                if self.allowed_extensions is not None:
                    ext = p.suffix.lower()
                    if ext not in self.allowed_extensions:
                        raise MiddlewareTermination(
                            f"FileValidator: extension {ext!r} not in allowed set "
                            f"{self.allowed_extensions}"
                        )
                if p.stat().st_size > self.max_file_size_bytes:
                    raise MiddlewareTermination(
                        f"FileValidator: {p.name} exceeds {self.max_file_size_bytes} byte limit"
                    )

        await call_next()
