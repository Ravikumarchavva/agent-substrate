"""Restate activity functions — executed inside ``ctx.run()`` for durability.

Merges:
- ``distributed/activities.py`` (agent ReAct activities)
- ``catalog/_temporal/activities.py`` (pipeline/chain activities)

All functions are plain async callables (not class methods).
DI globals are set once by :func:`configure` at worker startup.
Return values must be JSON-serializable dicts so Restate can journal them.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Module-level DI (set once by configure()) ───────────────────────────

_streaming: Any = None  # NATSStreamingBridge (optional)
_model_client: Any = None  # BaseModelClient (OpenAIClient)
_tools: Dict[str, Any] = {}  # name → BaseTool instance
_redis_memory: Any = None  # RedisMemory (connection pool)
_tool_schemas: List[Dict[str, Any]] = []
_catalog: Any = None  # CapabilityRegistry
_data_store: Any = None  # DataRefStore
_chain_runtime: Any = None  # ChainRuntime


def configure(
    *,
    streaming: Any = None,
    model_client: Any = None,
    tools: Optional[Dict[str, Any]] = None,
    redis_memory: Any = None,
    catalog: Any = None,
    data_store: Any = None,
    chain_runtime: Any = None,
) -> None:
    """Set DI globals for all activity functions.

    Called once at worker startup.
    """
    global _streaming, _model_client, _tools, _redis_memory
    global _tool_schemas, _catalog, _data_store, _chain_runtime

    if streaming is not None:
        _streaming = streaming
    if model_client is not None:
        _model_client = model_client
    if tools is not None:
        _tools = tools
        _tool_schemas = [t.get_openai_schema() for t in tools.values()]
    if redis_memory is not None:
        _redis_memory = redis_memory
    if catalog is not None:
        _catalog = catalog
    if data_store is not None:
        _data_store = data_store
    if chain_runtime is not None:
        _chain_runtime = chain_runtime

    logger.info(
        "Activities configured: %d tools, catalog=%s, chain_runtime=%s",
        len(_tools),
        type(_catalog).__name__ if _catalog else "None",
        type(_chain_runtime).__name__ if _chain_runtime else "None",
    )


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Return cached OpenAI-format tool schemas."""
    return _tool_schemas


# ── Agent workflow activities ────────────────────────────────────────────


async def restore_memory(thread_id: str) -> Dict[str, Any]:
    """Hydrate Redis with conversation history for a thread."""
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
    """Call the LLM and return a serializable result."""
    from raavan.core.messages import SystemMessage
    from raavan.integrations.memory.redis_memory import RedisMemory

    mem = RedisMemory.for_session(_redis_memory, thread_id)
    await mem.connect()
    messages = await mem.get_messages()

    if not messages or not isinstance(messages[0], SystemMessage):
        messages.insert(0, SystemMessage(content=system_instructions))

    response = await _model_client.generate(
        messages=messages,
        tools=tool_schemas if tool_schemas else None,
        model=model,
    )

    text: Optional[str] = None
    if response.content:
        for part in response.content:
            if isinstance(part, str):
                text = part
                break

    # Publish text_delta (ephemeral — skipped on replay)
    if text and _streaming is not None:
        await _streaming.publish(
            thread_id,
            {"type": "text_delta", "content": text, "partial": False},
        )

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
    """Execute a tool and return a serializable result."""
    tool = _tools.get(tool_name)
    if tool is None:
        return {"content": f"Tool '{tool_name}' not found", "is_error": True}

    # Publish tool_call event (ephemeral)
    if _streaming is not None:
        await _streaming.publish(
            thread_id,
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "input": arguments,
                "call_id": str(uuid.uuid4()),
                "idempotency_key": idempotency_key,
            },
        )

    try:
        result = await asyncio.wait_for(
            tool.run(**arguments),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        error_text = f"Tool '{tool_name}' timed out after {timeout_seconds}s"
        if _streaming is not None:
            await _streaming.publish(
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
        if _streaming is not None:
            await _streaming.publish(
                thread_id,
                {
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "result": error_text,
                    "error": error_text,
                },
            )
        return {"content": error_text, "is_error": True}

    # Serialize ToolResult
    content_text = ""
    if result.content:
        parts = []
        for block in result.content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        content_text = "\n".join(parts)

    if _streaming is not None:
        await _streaming.publish(
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
    """Persist a message to Redis memory."""
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
    """Persist a tool execution result to Redis memory."""
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


async def publish_event(
    thread_id: str,
    event: Dict[str, Any],
) -> None:
    """Publish an SSE event via the streaming bridge (ephemeral)."""
    if _streaming is not None:
        await _streaming.publish(thread_id, event)


# ── Pipeline/chain activities ────────────────────────────────────────────


async def execute_adapter_step(step_input: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single adapter step within a pipeline."""
    adapter_name = step_input["adapter_name"]
    action = step_input.get("action", "execute")
    inputs = step_input.get("inputs", {})

    logger.info("Executing adapter step: %s.%s", adapter_name, action)
    start = time.monotonic()

    if _catalog is None:
        return {"error": "Catalog not configured", "success": False}

    entry = _catalog.get(adapter_name)
    if entry is None or entry.tool is None:
        return {"error": f"Adapter '{adapter_name}' not found", "success": False}

    try:
        result = await entry.tool.run(**inputs)
        duration_ms = int((time.monotonic() - start) * 1000)

        output: Dict[str, Any] = {
            "success": True,
            "content": result.content if hasattr(result, "content") else str(result),
            "duration_ms": duration_ms,
        }

        # Store large results as DataRef
        if (
            _data_store
            and hasattr(result, "content")
            and len(str(result.content)) > 4096
        ):
            ref = await _data_store.store(
                data=str(result.content).encode(),
                content_type="text/plain",
            )
            output["data_ref_id"] = str(ref.ref_id)
            output["content"] = f"[DataRef: {ref.ref_id}]"

        return output
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception("Adapter step %s failed", adapter_name)
        return {"error": str(exc), "success": False, "duration_ms": duration_ms}


async def execute_code_chain(chain_input: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a code-based adapter chain via ChainRuntime."""
    code = chain_input["code"]
    timeout = chain_input.get("timeout", 120)

    logger.info("Executing code chain (timeout=%ds)", timeout)

    if _chain_runtime is None:
        return {"error": "ChainRuntime not available", "success": False}

    try:
        result = await _chain_runtime.execute_script(code, timeout=timeout)
        return {
            "success": result.error is None,
            "outputs": [str(o) for o in result.outputs],
            "data_refs": [str(r.ref_id) for r in result.data_refs],
            "logs": result.logs,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }
    except Exception as exc:
        logger.exception("Code chain execution failed")
        return {"error": str(exc), "success": False}
