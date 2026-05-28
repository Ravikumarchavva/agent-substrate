from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from ravi.kernel.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.kernel.plugin import register_guardrail


@register_guardrail("tool_call_validation")
class ToolCallValidationGuardrail(BaseGuardrail):
    """Validate tool calls against allow/block lists and argument schemas.

    Args:
        allowed_tools: If set, only these tools may be called.
        blocked_tools: These tools are always blocked.
        blocked_argument_patterns: {tool_name: {arg_name: [regex_patterns]}}
        tripwire: Hard stop on violation.
    """

    name = "tool_call_validation"
    description = "Validates tool calls against allow/block lists and argument patterns"
    guardrail_type = GuardrailType.TOOL_CALL

    def __init__(
        self,
        *,
        allowed_tools: Optional[Set[str]] = None,
        blocked_tools: Optional[Set[str]] = None,
        blocked_argument_patterns: Optional[Dict[str, Dict[str, List[str]]]] = None,
        tripwire: bool = True,
    ):
        self.allowed_tools = allowed_tools
        self.blocked_tools = blocked_tools or set()
        self.tripwire = tripwire

        self._arg_patterns: Dict[str, Dict[str, List[re.Pattern]]] = {}
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
            return self._fail(
                f"Tool '{tool_name}' is blocked",
                tripwire=self.tripwire,
                tool_name=tool_name,
            )

        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return self._fail(
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
                        return self._fail(
                            f"Blocked argument pattern in {tool_name}.{arg_name}: "
                            f"'{match.group()[:40]}'",
                            tripwire=self.tripwire,
                            tool_name=tool_name,
                            argument_name=arg_name,
                            matched_pattern=pattern.pattern,
                        )

        return self._pass(f"Tool call '{tool_name}' validated")
