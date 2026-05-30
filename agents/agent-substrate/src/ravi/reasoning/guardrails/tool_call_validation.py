from __future__ import annotations

import re

from ravi.reasoning.guardrails._contracts import (
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
    _fail,
    _pass,
)


class ToolCallValidationGuardrail:
    """Validate tool calls against allow/block lists and argument schemas."""

    name = "tool_call_validation"
    description = "Validates tool calls against allow/block lists and argument patterns"
    guardrail_type = GuardrailType.TOOL_CALL

    def __init__(
        self,
        *,
        allowed_tools: set[str] | None = None,
        blocked_tools: set[str] | None = None,
        blocked_argument_patterns: dict[str, dict[str, list[str]]] | None = None,
        tripwire: bool = True,
    ):
        self.allowed_tools = allowed_tools
        self.blocked_tools = blocked_tools or set()
        self.tripwire = tripwire
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

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        tool_name = ctx.tool_name or ""
        tool_args = ctx.tool_arguments or {}

        if tool_name in self.blocked_tools:
            return _fail(
                self.name,
                self.guardrail_type,
                f"Tool '{tool_name}' is blocked",
                tripwire=self.tripwire,
                tool_name=tool_name,
            )
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return _fail(
                self.name,
                self.guardrail_type,
                f"Tool '{tool_name}' is not in the allowed list",
                tripwire=self.tripwire,
                tool_name=tool_name,
                allowed_tools=sorted(self.allowed_tools),
            )
        if tool_name in self._arg_patterns:
            for arg_name, patterns in self._arg_patterns[tool_name].items():
                arg_value = str(tool_args.get(arg_name, ""))
                for pattern in patterns:
                    match = pattern.search(arg_value)
                    if match:
                        return _fail(
                            self.name,
                            self.guardrail_type,
                            f"Blocked argument pattern in {tool_name}.{arg_name}: "
                            f"'{match.group()[:40]}'",
                            tripwire=self.tripwire,
                            tool_name=tool_name,
                            argument_name=arg_name,
                            matched_pattern=pattern.pattern,
                        )
        return _pass(self.name, self.guardrail_type, f"Tool call '{tool_name}' validated")
