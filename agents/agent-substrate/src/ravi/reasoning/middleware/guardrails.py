"""GuardrailsMiddleware — runs reasoning-layer guardrails at LLM and tool stages."""
from __future__ import annotations

from typing import Any

from ravi.kernel import ToolUseBlock
from ravi.logger import setup_logging
from ravi.reasoning.guardrails._contracts import GuardrailContext, GuardrailType
from ravi.reasoning.guardrails.runner import run_guardrails
from ravi.reasoning.middleware._contracts import MiddlewareContext, MiddlewareStage

logger = setup_logging()


class GuardrailsMiddleware:
    """Runs guardrails at LLM_CALL (input/output) and TOOL_EXECUTION (tool_call) stages."""

    def __init__(
        self,
        input_guardrails: list[object] | None = None,
        output_guardrails: list[object] | None = None,
        tool_call_guardrails: list[object] | None = None,
    ) -> None:
        self.input_guardrails = list(input_guardrails or [])
        self.output_guardrails = list(output_guardrails or [])
        self.tool_call_guardrails = list(tool_call_guardrails or [])

        # Move any TOOL_CALL-typed guardrails into the right bucket
        for bucket in (self.input_guardrails, self.output_guardrails):
            for g in bucket:
                if getattr(g, "guardrail_type", None) == GuardrailType.TOOL_CALL:
                    if g not in self.tool_call_guardrails:
                        self.tool_call_guardrails.append(g)
        self.input_guardrails = [
            g for g in self.input_guardrails
            if getattr(g, "guardrail_type", None) != GuardrailType.TOOL_CALL
        ]
        self.output_guardrails = [
            g for g in self.output_guardrails
            if getattr(g, "guardrail_type", None) != GuardrailType.TOOL_CALL
        ]

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        if ctx.stage == MiddlewareStage.LLM_CALL and self.input_guardrails:
            if ctx.metadata.get("input_guardrails_run"):
                return ctx
            gctx = GuardrailContext(
                agent_name=ctx.agent_name,
                run_id=ctx.run_id,
                input_text=ctx.input_text,
            )
            results = await run_guardrails(
                self.input_guardrails, gctx, guardrail_type=GuardrailType.INPUT
            )
            ctx.metadata["input_guardrails_run"] = True
            ctx.metadata.setdefault("guardrail_results", []).extend(results)

        elif ctx.stage == MiddlewareStage.TOOL_EXECUTION and self.tool_call_guardrails:
            gctx = GuardrailContext(
                agent_name=ctx.agent_name,
                run_id=ctx.run_id,
                tool_name=ctx.tool_name,
                tool_arguments=ctx.tool_args,
            )
            results = await run_guardrails(
                self.tool_call_guardrails, gctx, guardrail_type=GuardrailType.TOOL_CALL
            )
            ctx.metadata.setdefault("guardrail_results", []).extend(results)

        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        if ctx.stage != MiddlewareStage.LLM_CALL or not self.output_guardrails:
            return result
        # result is list[ContentBlock]; only run output guardrails if no tool calls
        if not isinstance(result, list):
            return result
        has_tool_use = any(isinstance(b, ToolUseBlock) for b in result)
        if has_tool_use:
            return result

        from ravi.kernel import content_blocks_to_str
        output_text = content_blocks_to_str(result)
        gctx = GuardrailContext(
            agent_name=ctx.agent_name,
            run_id=ctx.run_id,
            output_text=output_text,
        )
        results = await run_guardrails(
            self.output_guardrails, gctx, guardrail_type=GuardrailType.OUTPUT
        )
        ctx.metadata.setdefault("guardrail_results", []).extend(results)
        return result
