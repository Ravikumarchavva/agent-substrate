"""Restate-journaled activity functions for durable agent execution.

Each function is called inside ``ctx.run("name", fn, args=(...))`` in the
:mod:`raavan.distributed.workflow`.  Restate journals the *return value*,
so on replay completed activities are skipped (exactly-once semantics).

All return values are plain JSON-serializable dicts — no framework objects
pass through the Restate journal.

DI globals are initialised once by :func:`configure` (called from
:mod:`raavan.distributed.worker` at startup).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Module-level dependency injection ────────────────────────────────────
# Set once by configure() — activities are plain functions, not methods,
# so DI must be module-scoped.

_nats: Any = None  # NATSStreamingBridge
_model_client: Any = None  # BaseModelClient (OpenAIClient)
_tools: Dict[str, Any] = {}  # name → BaseTool instance
_redis_memory: Any = None  # RedisMemory (connection pool)
_tool_schemas: List[Dict[str, Any]] = []  # cached OpenAI tool schemas


def configure(
    *,
    nats: Any,
    model_client: Any,
    tools: Dict[str, Any],
    redis_memory: Any,
) -> None:
    """Set DI globals for all activity functions.

    Called once at worker startup by :func:`raavan.distributed.worker.main`.
    """
    global _nats, _model_client, _tools, _redis_memory, _tool_schemas
    _nats = nats
    _model_client = model_client
    _tools = tools
    _redis_memory = redis_memory
    _tool_schemas = [t.get_openai_schema() for t in tools.values()]
    logger.info(
        "Activities configured: %d tools, model_client=%s",
        len(tools),
        type(model_client).__name__,
    )


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Return cached OpenAI-format tool schemas."""
    return _tool_schemas


# ── Activity functions ───────────────────────────────────────────────────


async def restore_memory(thread_id: str) -> Dict[str, Any]:
    """Hydrate Redis with conversation history for a thread.

    Returns ``{"message_count": int, "source": str}`` (journaled).
    """
    from raavan.integrations.memory.redis_memory import RedisMemory

    mem = RedisMemory.for_session(_redis_memory, thread_id)
    await mem.connect()
    await mem.restore()
    count = await mem.size()
    return {"message_count": count, "source": "redis"}


async def do_llm_call(
    thread_id: str,
    model: str,
    tool_schemas: List[Dict[str, Any]],
    system_instructions: str,
) -> Dict[str, Any]:
    """Call the LLM and return a serializable result.

    Publishes a ``text_delta`` event to NATS (ephemeral on replay).
    The journaled return value contains the full response text and any
    tool calls.
    """
    from raavan.core.messages import SystemMessage
    from raavan.integrations.memory.redis_memory import RedisMemory

    mem = RedisMemory.for_session(_redis_memory, thread_id)
    await mem.connect()
    messages = await mem.get_messages()

    # Ensure system message is first
    if not messages or not isinstance(messages[0], SystemMessage):
        messages.insert(0, SystemMessage(content=system_instructions))

    response = await _model_client.generate(
        messages=messages,
        tools=tool_schemas if tool_schemas else None,
        model=model,
    )

    # Extract plain text from AssistantMessage.content
    text: Optional[str] = None
    if response.content:
        for part in response.content:
            if isinstance(part, str):
                text = part
                break

    # Publish text_delta to NATS (ephemeral — skipped on replay)
    if text and _nats is not None:
        await _nats.publish(
            thread_id,
            {
                "type": "text_delta",
                "content": text,
                "partial": False,
            },
        )

    # Serialize tool calls for the Restate journal
    tool_calls: List[Dict[str, Any]] = []
    if response.tool_calls:
        for tc in response.tool_calls:
            tool_calls.append(
                {
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
            )

    return {"content": text, "tool_calls": tool_calls}


async def do_tool_exec(
    tool_name: str,
    arguments: Dict[str, Any],
    thread_id: str,
    timeout_seconds: float,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a tool and return a serializable result.

    Publishes ``tool_call`` and ``tool_result`` events to NATS
    (ephemeral on replay).  The journaled return value contains the tool
    output text and error flag.
    """
    import asyncio

    tool = _tools.get(tool_name)
    if tool is None:
        return {
            "content": f"Tool '{tool_name}' not found",
            "is_error": True,
        }

    # Publish tool_call event (ephemeral)
    if _nats is not None:
        await _nats.publish(
            thread_id,
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "input": arguments,
                "call_id": str(uuid.uuid4()),
                "idempotency_key": idempotency_key,
            },
        )

    # Execute with timeout
    try:
        result = await asyncio.wait_for(
            tool.run(**arguments),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        error_text = f"Tool '{tool_name}' timed out after {timeout_seconds}s"
        if _nats is not None:
            await _nats.publish(
                thread_id,
                {
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "result": error_text,
                    "error": error_text,
                },
            )
        return {"content": error_text, "is_error": True}
    except Exception as exc:
        error_text = f"Tool '{tool_name}' failed: {exc}"
        if _nats is not None:
            await _nats.publish(
                thread_id,
                {
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "result": error_text,
                    "error": error_text,
                },
            )
        return {"content": error_text, "is_error": True}

    # Serialize ToolResult for journal
    content_text = ""
    if result.content:
        parts = []
        for block in result.content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        content_text = "\n".join(parts)

    # Publish tool_result event (ephemeral)
    if _nats is not None:
        await _nats.publish(
            thread_id,
            {
                "type": "tool_result",
                "tool_name": tool_name,
                "result": content_text,
                "metadata": {},
            },
        )

    return {"content": content_text, "is_error": result.is_error}


async def persist_message(
    thread_id: str,
    role: str,
    content: str,
) -> Dict[str, Any]:
    """Persist a message to Redis memory for a thread.

    Supports ``"user"``, ``"assistant"``, and ``"tool_result"`` roles.
    Returns ``{"persisted": True}`` (journaled).
    """
    from raavan.core.messages import (
        AssistantMessage,
        ToolExecutionResultMessage,
        UserMessage,
    )
    from raavan.integrations.memory.redis_memory import RedisMemory

    mem = RedisMemory.for_session(_redis_memory, thread_id)
    await mem.connect()

    if role == "user":
        await mem.add_message(UserMessage(content=[content]))
    elif role == "assistant":
        await mem.add_message(AssistantMessage(content=[content]))
    elif role == "tool_result":
        await mem.add_message(
            ToolExecutionResultMessage(
                content=content,
                tool_call_id=str(uuid.uuid4()),
                name="workflow_tool",
            )
        )

    return {"persisted": True}


async def persist_tool_result(
    thread_id: str,
    tool_name: str,
    tool_call_id: str,
    content: str,
    is_error: bool,
) -> Dict[str, Any]:
    """Persist a tool execution result to Redis memory.

    Separate from :func:`persist_message` to carry ``tool_call_id`` and
    ``tool_name`` metadata.
    """
    from raavan.core.messages import ToolExecutionResultMessage
    from raavan.integrations.memory.redis_memory import RedisMemory

    mem = RedisMemory.for_session(_redis_memory, thread_id)
    await mem.connect()

    await mem.add_message(
        ToolExecutionResultMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
        )
    )

    return {"persisted": True, "is_error": is_error}
