"""Guardrails middleware — composable pre/post processing guardrail execution."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from ravi.core.middleware.base import BaseMiddleware, MiddlewareContext, MiddlewareStage
from ravi.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailType,
)
from ravi.core.guardrails.runner import run_guardrails
from ravi.core.messages.client_messages import AssistantMessage

logger = logging.getLogger("ravi.core.middleware.guardrails")


class GuardrailsMiddleware(BaseMiddleware):
    """Middleware that executes agent guardrails at LLM_CALL and TOOL_EXECUTION stages.

    Decouples guardrail execution completely from the main agent loop.
    Input guardrails run during before() (pre-LLM), and output guardrails
    run during after() (post-LLM).
    Tool-call guardrails run during before() at TOOL_EXECUTION stage.
    """

    def __init__(
        self,
        input_guardrails: Optional[List[BaseGuardrail]] = None,
        output_guardrails: Optional[List[BaseGuardrail]] = None,
        tool_call_guardrails: Optional[List[BaseGuardrail]] = None,
    ) -> None:
        super().__init__("guardrails")
        self.input_guardrails = input_guardrails or []
        self.output_guardrails = output_guardrails or []
        self.tool_call_guardrails = tool_call_guardrails or []

        # If any of the input/output guardrails are actually tool_call guardrails,
        # move/copy them to tool_call_guardrails to keep separation clean.
        self.tool_call_guardrails.extend(
            [
                g
                for g in self.input_guardrails
                if getattr(g, "guardrail_type", None) == GuardrailType.TOOL_CALL
            ]
        )
        self.tool_call_guardrails.extend(
            [
                g
                for g in self.output_guardrails
                if getattr(g, "guardrail_type", None) == GuardrailType.TOOL_CALL
            ]
        )

        # Filter out tool call guardrails from input/output guardrail lists
        self.input_guardrails = [
            g
            for g in self.input_guardrails
            if getattr(g, "guardrail_type", None) != GuardrailType.TOOL_CALL
        ]
        self.output_guardrails = [
            g
            for g in self.output_guardrails
            if getattr(g, "guardrail_type", None) != GuardrailType.TOOL_CALL
        ]

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        """Run input or tool-call guardrails before the LLM call or tool execution."""
        if ctx.stage == MiddlewareStage.LLM_CALL and self.input_guardrails:
            # Prevent redundant execution (only run once per agent run)
            if ctx.metadata.get("input_guardrails_run"):
                return ctx

            guardrail_ctx = GuardrailContext(
                agent_name=ctx.agent_name,
                run_id=ctx.run_id,
                input_text=ctx.input_text,
            )
            # This will raise GuardrailTripwireError if tripped
            results = await run_guardrails(
                self.input_guardrails,
                guardrail_ctx,
                guardrail_type=GuardrailType.INPUT,
            )
            ctx.metadata["input_guardrails_run"] = True
            ctx.metadata.setdefault("guardrail_results", []).extend(results)

        elif ctx.stage == MiddlewareStage.TOOL_EXECUTION and self.tool_call_guardrails:
            # Run tool-call guardrails
            guardrail_ctx = GuardrailContext(
                agent_name=ctx.agent_name,
                run_id=ctx.run_id,
                tool_name=ctx.tool_name,
                tool_arguments=ctx.tool_args,
            )
            # This will raise GuardrailTripwireError if tripped
            results = await run_guardrails(
                self.tool_call_guardrails,
                guardrail_ctx,
                guardrail_type=GuardrailType.TOOL_CALL,
            )
            ctx.metadata.setdefault("guardrail_results", []).extend(results)

        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        """Run output guardrails after the LLM call."""
        if (
            ctx.stage == MiddlewareStage.LLM_CALL
            and self.output_guardrails
            and isinstance(result, AssistantMessage)
        ):
            # Only run output guardrails on final answer (no tool calls requested)
            if not result.tool_calls:
                # Extract output text
                output_text = None
                if result.content:
                    parts = []
                    for item in result.content:
                        if isinstance(item, str):
                            parts.append(item)
                        elif hasattr(item, "text"):
                            parts.append(item.text)
                        elif isinstance(item, dict) and "text" in item:
                            parts.append(str(item["text"]))
                    output_text = "\n".join(parts)

                guardrail_ctx = GuardrailContext(
                    agent_name=ctx.agent_name,
                    run_id=ctx.run_id,
                    output_text=output_text,
                    raw_message=result,
                )
                # This will raise GuardrailTripwireError if tripped
                results = await run_guardrails(
                    self.output_guardrails,
                    guardrail_ctx,
                    guardrail_type=GuardrailType.OUTPUT,
                )
                ctx.metadata.setdefault("guardrail_results", []).extend(results)

        return result
