"""ConditionWorkflowRunner — expression-based branching workflow executor.

Handles pipelines that contain a ``condition`` node.  At runtime:

1. The upstream agent runs normally and produces a text response.
2. The ``ConditionWorkflowRunner`` evaluates each condition expression against
   a simple context dict derived from the agent's output.
3. The first matching branch agent is selected and executed.
4. If no branch matches, the ``else_agent`` is used (if configured).

Condition expressions are safe Python-subset expressions evaluated with
``ast.literal_eval`` semantics — they may reference the key ``output``
(the upstream agent's text reply) plus any keyword extracted by a very
lightweight text matcher.

Example pipeline::

    start → ClassifierAgent → Condition(intent=="billing" / intent=="tech") → BillingAgent / TechAgent / GeneralAgent

The upstream agent should return structured text; the condition runner
does a best-effort keyword scan to populate the context dict.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import re
from typing import Any, Dict, List, Optional

from ravi.extensions.agents.assistant.agent import AssistantAgent
from ravi.extensions.pipelines._expr_eval import safe_eval

logger = setup_logging()


class ConditionWorkflowRunner:
    """Runs a condition branch pipeline.

    Parameters
    ----------
    upstream_agent:
        The agent that runs first; its output is used to evaluate conditions.
    conditions:
        List of dicts with ``{"expression": str, "label": str}``.
        Expressions are evaluated against a simple context dict.
    branch_agents:
        Mapping of branch handle/index to ``AssistantAgent`` (e.g. ``"cond-0"``).
    else_agent:
        Agent to use when no condition matches.  If ``None``, the upstream
        agent's output is returned as-is.
    """

    def __init__(
        self,
        *,
        upstream_agent: Optional[AssistantAgent],
        conditions: List[Dict[str, Any]],
        branch_agents: Dict[str, AssistantAgent],
        else_agent: Optional[AssistantAgent],
    ) -> None:
        self.upstream_agent = upstream_agent
        self.conditions = conditions
        self.branch_agents = branch_agents
        self.else_agent = else_agent

    # ------------------------------------------------------------------
    # Public API (matches AssistantAgent.run / run_stream signature subset)
    # ------------------------------------------------------------------

    async def run(self, input_text: str) -> Any:
        """Run the upstream agent, evaluate conditions, dispatch to branch."""
        # 1. Run upstream
        if self.upstream_agent is None:
            return type("R", (), {"output": "No agent configured."})()

        upstream_result = await self.upstream_agent.run(input_text)
        _raw = getattr(upstream_result, "output", upstream_result)
        # Normalize to str (AssistantAgent output may be a list from AssistantMessage.content)
        if isinstance(_raw, list):
            upstream_output = " ".join(str(x) for x in _raw if x)
        else:
            upstream_output = str(_raw)

        # 2. Evaluate conditions
        ctx = self._build_context(input_text, upstream_output)
        selected_agent, branch_name = self._evaluate(ctx)

        if selected_agent is None:
            logger.info("No condition matched; returning upstream output")
            return upstream_result

        logger.info("Condition branch selected: %r", branch_name)

        # 3. Run the selected branch agent with the upstream output as context
        branch_input = (
            f"Context from previous step:\n{upstream_output}\n\n"
            f"Original user request:\n{input_text}"
        )
        return await selected_agent.run(branch_input)

    async def run_stream(self, input_text: str):  # type: ignore[override]
        """Stream version — runs upstream blocking then streams the branch."""
        from ravi.kernel.messages._types import TextDeltaChunk

        if self.upstream_agent is None:
            yield TextDeltaChunk(text="No agent configured.")
            return

        # Run upstream blocking to get the output for condition evaluation
        upstream_result = await self.upstream_agent.run(input_text)
        _raw = getattr(upstream_result, "output", upstream_result)
        if isinstance(_raw, list):
            upstream_output = " ".join(str(x) for x in _raw if x)
        else:
            upstream_output = str(_raw)

        # Announce which path was taken
        ctx = self._build_context(input_text, upstream_output)
        selected_agent, branch_name = self._evaluate(ctx)

        # Yield the upstream output first
        yield TextDeltaChunk(text=upstream_output)

        if selected_agent is None:
            return

        # Then stream the branch agent's response
        yield TextDeltaChunk(text=f"\n\n[Branch: {branch_name}]\n")
        branch_input = (
            f"Context from previous step:\n{upstream_output}\n\n"
            f"Original user request:\n{input_text}"
        )
        async for chunk in selected_agent.run_stream(branch_input):
            yield chunk

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_context(self, input_text: str, output: str) -> Dict[str, Any]:
        """Build a simple evaluation context from agent I/O."""
        # Ensure both are plain strings
        input_s = str(input_text) if not isinstance(input_text, str) else input_text
        output_s = str(output) if not isinstance(output, str) else output
        ctx: Dict[str, Any] = {
            "input": input_s.lower(),
            "output": output_s.lower(),
        }
        # Try to extract key=value pairs from the output (e.g. "intent: billing")
        for m in re.finditer(r'(\w+)\s*[:=]\s*["\']?(\w+)["\']?', output_s):
            ctx[m.group(1).lower()] = m.group(2).lower()
        return ctx

    def _evaluate(self, ctx: Dict[str, Any]) -> tuple[Optional[AssistantAgent], str]:
        """Evaluate condition expressions and return the matching branch agent."""
        for idx, cond in enumerate(self.conditions):
            expr = cond.get("expression", "")
            label = cond.get("label", f"Branch {idx + 1}")
            handle = f"cond-{idx}"
            if safe_eval(expr, ctx):
                agent = self.branch_agents.get(handle) or self.branch_agents.get(label)
                if agent:
                    return agent, label

        if self.else_agent:
            return self.else_agent, "else"

        return None, ""
