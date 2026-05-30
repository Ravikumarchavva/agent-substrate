"""Streaming/SSE emission helpers for ReActAgent.run_stream().

Extracts the inner streaming loop body so the agent class stays thin.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, cast

from ravi.exceptions import GuardrailTripwireError
from ravi.kernel import CompletionEvent, TextDelta
from ravi.fabric.llm.client import LLMClient
from ravi.reasoning.agents.assistant._guardrail_runner import (
    build_tool_blocked_message,
    check_output_guardrails,
    check_tool_call_guardrails,
)
from ravi.reasoning.agents.assistant._legacy_stubs import (
    AssistantMessage,
    ParsedToolCall,
    ToolExecutionResultMessage,
)
from ravi.shared.observability import global_metrics, logger

# Deleted type stubs
BaseGuardrail = object
BaseModelClient = LLMClient  # alias
CompletionChunk = CompletionEvent  # alias
TextDeltaChunk = TextDelta  # alias

def parse_tool_call(*args: Any, **kwargs: Any) -> ParsedToolCall:  # type: ignore[return]
    raise NotImplementedError("parse_tool_call: TODO(agent-migration)")


# ---------------------------------------------------------------------------
# Stream one LLM generation call, yielding chunks as they arrive
# ---------------------------------------------------------------------------


async def stream_llm_generation(
    *,
    model_client: BaseModelClient,
    messages: List[Any],
    tool_schemas: Optional[List[Dict[str, Any]]],
    response_schema: Optional[type],
    history: Any,
    session_id: str,
    kwargs: Dict[str, Any],
) -> AsyncIterator[Any]:
    """Yield chunks from model_client.generate_stream and return the final message.

    This is an async generator that yields ``TextDeltaChunk`` / ``CompletionChunk``
    objects. The caller reads the yielded ``CompletionChunk`` to get
    ``final_response_obj``.
    """
    llm_t0 = asyncio.get_event_loop().time()
    final_response_obj: Optional[AssistantMessage] = None
    partial_text: str = ""

    try:
        async for chunk in model_client.generate_stream(
            messages=messages,
            tools=tool_schemas or None,
            tool_choice="auto" if tool_schemas else None,
            response_format=response_schema,
            **kwargs,
        ):
            yield chunk

            if isinstance(chunk, TextDeltaChunk):
                partial_text += chunk.text
            elif isinstance(chunk, CompletionChunk):
                final_response_obj = chunk.message

        if final_response_obj:
            await history.save_messages(session_id, [final_response_obj])

        llm_t1 = asyncio.get_event_loop().time()
        global_metrics.record_histogram(
            "llm_latency",
            llm_t1 - llm_t0,
            tags={"model": getattr(model_client, "model", "unknown")},
        )
    except asyncio.CancelledError:
        if final_response_obj is not None:
            await history.save_messages(session_id, [final_response_obj])
        elif partial_text:
            await history.save_messages(
                session_id,
                [
                    AssistantMessage(
                        role="assistant",
                        content=[partial_text],
                        finish_reason="cancelled",
                    )
                ],
            )
        raise
    except Exception as e:
        global_metrics.increment_counter("llm_errors", tags={"error": type(e).__name__})
        raise


# ---------------------------------------------------------------------------
# Handle the final (no tool-calls) streaming response
# ---------------------------------------------------------------------------


async def handle_stream_final_response(
    *,
    response: AssistantMessage,
    output_guardrails: List[BaseGuardrail],
    agent_name: str,
    run_id: str,
    model_client: BaseModelClient,
    model_context: Any,
    history: Any,
    session_id: str,
    input_text: str,
    response_schema: Optional[type],
    stream_pub: Any,
    extract_text_fn: Any,
) -> AsyncIterator[Any]:
    """Yield output-guardrail error or structured-output chunk for the final turn."""
    # Output guardrails
    try:
        if output_guardrails:
            output_text = extract_text_fn(response)
            await check_output_guardrails(
                guardrails=output_guardrails,
                agent_name=agent_name,
                run_id=run_id,
                output_text=output_text,
                raw_message=response,
            )
    except GuardrailTripwireError as e:
        logger.error(f"[{agent_name}] Output guardrail tripwire (stream): {e.message}")
        yield CompletionChunk(
            message=AssistantMessage(
                role="assistant",
                content=[f"Response blocked: {e.message}"],
                finish_reason="guardrail_tripped",
            ),
            metadata={
                "guardrail_tripped": True,
                "guardrail": e.guardrail_name,
            },
        )
        if stream_pub is not None:
            await stream_pub.close("guardrail_tripped")
        return

    if stream_pub is not None:
        await stream_pub.close()

    # Structured output extraction
    if response_schema is not None:
        from ravi.reasoning.agents.assistant._legacy_stubs import StructuredOutputChunk

        _parsed = getattr(response, "parsed", None)
        if _parsed is not None:
            from ravi.reasoning.agents.assistant._legacy_stubs import StructuredOutputResult

            raw_text = extract_text_fn(response)
            yield StructuredOutputChunk(
                result=StructuredOutputResult(
                    parsed=_parsed,
                    raw_text=raw_text,
                    model=getattr(model_client, "model", None),
                )
            )
        else:
            context_messages = await model_context.build(
                session_id=session_id,
                current_input=input_text,
                raw_messages=await history.load_messages(session_id),
                model_client=model_client,
            )
            structured_result = await model_client.generate(
                context_messages,
                response_format=response_schema,
            )
            from ravi.reasoning.agents.assistant._legacy_stubs import StructuredOutputResult as _SOR

            yield StructuredOutputChunk(result=cast(_SOR[Any], structured_result))


# ---------------------------------------------------------------------------
# Process tool calls within a streaming step
# ---------------------------------------------------------------------------


async def process_stream_tool_calls(
    *,
    response: AssistantMessage,
    input_guardrails: List[BaseGuardrail],
    output_guardrails: List[BaseGuardrail],
    agent_name: str,
    run_id: str,
    step_num: int,
    history: Any,
    session_id: str,
    execute_tool_fn: Any,
    stream_pub: Any,
    tool_timeout: Optional[float] = None,
) -> AsyncIterator[ToolExecutionResultMessage]:
    """Process tool calls in a streaming step, yielding result messages."""
    if not response.tool_calls:
        return
    for tc_raw in response.tool_calls:
        parsed = parse_tool_call(tc_raw)

        tool_blocked = False
        try:
            await check_tool_call_guardrails(
                input_guardrails=input_guardrails,
                output_guardrails=output_guardrails,
                agent_name=agent_name,
                run_id=run_id,
                parsed=parsed,
            )
        except GuardrailTripwireError as e:
            logger.error(
                f"[{agent_name}] Tool-call guardrail tripwire (stream): {e.message}"
            )
            tool_blocked = True
            tool_msg = build_tool_blocked_message(parsed, e.message)
            await history.save_messages(session_id, [tool_msg])
            yield tool_msg
            if stream_pub is not None:
                await stream_pub.emit(tool_msg)

        if not tool_blocked:
            coro = execute_tool_fn(parsed, step_num)
            if tool_timeout is not None:
                try:
                    _, tool_msg = await asyncio.wait_for(coro, timeout=tool_timeout)
                except asyncio.TimeoutError:
                    from ravi.kernel.content import TextBlock
                    from ravi.reasoning.agents.assistant._legacy_stubs import (
                        ToolExecutionResultMessage,
                    )

                    tool_msg = ToolExecutionResultMessage(
                        content=[
                            TextBlock(
                                text=f"Tool '{parsed.name}' timed out after {tool_timeout}s"
                            )
                        ],
                        tool_call_id=parsed.call_id,
                        name=parsed.name,
                        is_error=True,
                    )
            else:
                _, tool_msg = await coro
            await history.save_messages(session_id, [tool_msg])
            yield tool_msg
            if stream_pub is not None:
                await stream_pub.emit(tool_msg)
