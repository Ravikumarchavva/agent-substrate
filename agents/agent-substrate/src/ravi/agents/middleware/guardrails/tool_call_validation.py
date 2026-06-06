from __future__ import annotations

import re
from typing import Callable, Awaitable

from ravi.agents.middleware._contracts import FunctionContext
from ravi.exceptions import MiddlewareTermination


class ToolCallValidationMiddleware:
    """Validate tool calls against allow/block lists and argument schemas."""

    def __init__(
        self,
        *,
        allowed_tools: set[str] | None = None,
        blocked_tools: set[str] | None = None,
        blocked_argument_patterns: dict[str, dict[str, list[str]]] | None = None,
    ):
        self.allowed_tools = allowed_tools
        self.blocked_tools = blocked_tools or set()
        self._arg_patterns: dict[str, dict[str, list[re.Pattern[str]]]] = {}

        if blocked_argument_patterns:
            for tool, args_map in blocked_argument_patterns.items():
                self._arg_patterns[tool] = {}
                for arg_name, patterns in args_map.items():
                    compiled = []
                    for p in patterns:
                        try:
                            compiled.append(re.compile(p, re.I))
                        except re.error as e:
                            raise ValueError(
                                f"Invalid blocked_argument_pattern regex for "
                                f"{tool}.{arg_name} '{p}': {e}"
                            ) from e
                    self._arg_patterns[tool][arg_name] = compiled

    async def process(self, context: FunctionContext, call_next: Callable[[], Awaitable[None]]) -> None:
        tool_name = context.function_name
        tool_args = context.arguments or {}

        if tool_name in self.blocked_tools:
            raise MiddlewareTermination(f"ToolCallValidation: Tool '{tool_name}' is blocked")

        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            raise MiddlewareTermination(f"ToolCallValidation: Tool '{tool_name}' is not in the allowed list")

        if tool_name in self._arg_patterns:
            for arg_name, patterns in self._arg_patterns[tool_name].items():
                arg_value = str(tool_args.get(arg_name, ""))
                for pattern in patterns:
                    match = pattern.search(arg_value)
                    if match:
                        raise MiddlewareTermination(
                            f"ToolCallValidation: Blocked argument pattern in {tool_name}.{arg_name}: '{match.group()[:40]}'"
                        )

        await call_next()
