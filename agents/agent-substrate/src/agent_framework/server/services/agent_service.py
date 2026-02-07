"""Agent service – creates agents with restored per-session memory.

Responsibilities:
  1. Build agent memory from persisted steps
  2. Create configured ReActAgent per thread
  3. Persist new messages to database after agent run
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from agent_framework.messages.base_message import BaseClientMessage
from agent_framework.model_clients.base_client import BaseModelClient
from agent_framework.tools.base_tool import BaseTool

from agent_framework.server.services import (
    create_step,
    load_messages_for_memory,
)


def _rebuild_memory(
    step_rows: List[Dict[str, Any]],
    system_instructions: str,
) -> UnboundedMemory:
    """Rebuild UnboundedMemory from persisted step rows.

    Maps each step type back to the proper framework message object.
    """
    memory = UnboundedMemory()

    # Always start with system message
    memory.add_message(SystemMessage(content=system_instructions))

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            # Skip – we already added the system message above
            continue

        elif step_type == "user_message":
            content_text = row.get("input") or ""
            memory.add_message(UserMessage(content=[content_text]))

        elif step_type == "assistant_message":
            output_text = row.get("output")
            content = [output_text] if output_text else None
            
            # Rebuild tool calls if stored
            tool_calls = None
            gen = row.get("generation") or {}
            if gen.get("tool_calls"):
                tool_calls = [
                    ToolCallMessage(**tc) for tc in gen["tool_calls"]
                ]

            memory.add_message(AssistantMessage(
                content=content,
                tool_calls=tool_calls,
                finish_reason=gen.get("finish_reason", "stop"),
            ))

        elif step_type == "tool_call":
            # Tool calls are embedded in assistant message, skip standalone
            pass

        elif step_type == "tool_result":
            tool_call_id = meta.get("tool_call_id", "")
            tool_name = row.get("name", "")
            output = row.get("output") or ""
            is_error = row.get("is_error") or False
            memory.add_message(ToolExecutionResultMessage(
                tool_call_id=tool_call_id,
                name=tool_name,
                content=[{"type": "text", "text": output}],
                isError=is_error,
            ))

    return memory


def create_agent_for_thread(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: UnboundedMemory,
    max_iterations: int = 10,
    verbose: bool = True,
) -> ReActAgent:
    """Create a ReActAgent with pre-loaded per-session memory."""
    return ReActAgent(
        name="ChatBot",
        description="A helpful AI assistant with tool access.",
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        max_iterations=max_iterations,
        verbose=verbose,
    )


async def load_agent_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    max_iterations: int = 10,
    verbose: bool = True,
) -> ReActAgent:
    """Load persisted conversation into an agent for the given thread."""
    step_rows = await load_messages_for_memory(db, thread_id)
    memory = _rebuild_memory(step_rows, system_instructions)
    return create_agent_for_thread(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        max_iterations=max_iterations,
        verbose=verbose,
    )


async def persist_user_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    content: str,
) -> uuid.UUID:
    """Save a user message step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="user_message",
        name="user",
        input=content,
    )
    return step.id


async def persist_assistant_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    message: AssistantMessage,
    *,
    parent_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Save an assistant message step and return its ID."""
    # Serialize tool calls for storage
    generation: Dict[str, Any] = {
        "finish_reason": message.finish_reason,
    }
    if message.usage:
        generation["usage"] = {
            "prompt_tokens": message.usage.prompt_tokens,
            "completion_tokens": message.usage.completion_tokens,
            "total_tokens": message.usage.total_tokens,
        }
    if message.tool_calls:
        generation["tool_calls"] = [tc.to_dict() for tc in message.tool_calls]

    output_text = None
    if message.content:
        # Extract text from multimodal content list
        texts = [c for c in message.content if isinstance(c, str)]
        output_text = "\n".join(texts) if texts else None

    step = await create_step(
        db,
        thread_id=thread_id,
        type="assistant_message",
        name="assistant",
        output=output_text,
        generation=generation,
        parent_id=parent_id,
    )
    return step.id


async def persist_tool_result(
    db: AsyncSession,
    thread_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    output: str,
    is_error: bool = False,
    *,
    parent_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Save a tool result step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="tool_result",
        name=tool_name,
        output=output,
        is_error=is_error,
        metadata={"tool_call_id": tool_call_id},
        parent_id=parent_id,
    )
    return step.id
