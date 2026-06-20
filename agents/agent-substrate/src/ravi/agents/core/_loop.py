"""Shared conversation primitives and helper functions for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ravi.kernel.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    content_blocks_to_str,
)
from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.message import ChatPayload, DataPayload, Message
from ravi.kernel.llm.llm import GenerationOptions

if TYPE_CHECKING:
    from ravi.agents.context.context import ContextConfig
    from ravi.agents.runtime.context import RunContext


def message_to_chat(msg: Message) -> ChatMessage:
    """Convert an inbox Message to a user ChatMessage."""
    payload = msg.payload
    if isinstance(payload, ChatPayload):
        return payload.message
    # Treat DataPayload or others as a text user turn
    if isinstance(payload, DataPayload):
        text = str(payload.data.get("text", payload.data))
    else:
        text = str(getattr(payload, "data", payload))
    return ChatMessage(role=Role.USER, content=[TextBlock(text=text)])


async def load_history(
    ctx_cfg: ContextConfig, agent_id: AgentId, session_id: str
) -> list[ChatMessage]:
    """Load session history and apply the compaction pipeline."""
    raw = await ctx_cfg.history.get_messages(agent_id, session_id=session_id)
    return list(await ctx_cfg.pipeline.compact(list(raw)))


async def persist_turns(
    ctx_cfg: ContextConfig,
    agent_id: AgentId,
    session_id: str,
    run_id: str,
    new_turns: list[ChatMessage],
) -> None:
    """Persist new turns using ContextConfig."""
    await ctx_cfg.history.append_many(
        agent_id, new_turns, session_id=session_id, run_id=run_id
    )


def final_text(messages: list[ChatMessage]) -> str:
    """Return the text of the last assistant turn."""
    for msg in reversed(messages):
        if msg.role == Role.ASSISTANT:
            return content_blocks_to_str(msg.content)  # type: ignore[arg-type]
    return ""


async def deliver(
    ctx: RunContext,
    src_msg: Message,
    result: dict[str, Any],
    *,
    sender: AgentId,
    output_topic: TopicId | None = None,
) -> None:
    """Deliver a result back to the sender or emit it to a topic."""
    session_id = src_msg.correlation_id or ctx.run_id
    if src_msg.reply_to:
        await ctx.reply(src_msg, result)
    elif output_topic is not None:
        out_msg = Message(
            target=output_topic,
            sender=sender,
            payload=DataPayload(data=result),
            correlation_id=session_id,
        )
        await ctx.emit(output_topic, out_msg)


async def summarize(ctx: RunContext, text: str, *, instructions: str) -> str:
    """Run a journaled LLM summarization pass."""
    messages = [
        ChatMessage(role=Role.USER, content=[TextBlock(text=text)]),
    ]
    options = GenerationOptions(system_instructions=instructions)
    resp = await ctx.llm(messages, options=options)
    return content_blocks_to_str(resp.content)  # type: ignore[arg-type]
