"""Shared conversation primitives and helper functions for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from substrate.kernel.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    content_blocks_to_str,
)
from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.messaging.message import ChatPayload, DataPayload, Message
from substrate.kernel.llm.llm import GenerationOptions

if TYPE_CHECKING:
    from substrate.agents.context.context import ContextConfig
    from substrate.agents.runtime.context import RunContext


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


async def log_user_message(
    ctx: RunContext, msg: Message, user_turn: ChatMessage
) -> int:
    """Journal the turn that started this run as a ``user.message`` EventLogProtocol
    entry, so the log is a self-complete record of the conversation (history
    is projected from it — see ``serving/stream/history.py``).

    ``msg.metadata["display_text"]``/``["attachments"]`` (set by the serving
    layer when it augments the LLM-input content with file context) win when
    present — the user should see what they actually typed, not the
    augmented prompt the model received. Falls back to the plain turn text
    for any caller that doesn't set that metadata (e.g. ``Runtime.run()``).
    ``log_once``, not ``_log``: this call itself re-executes on every replay
    attempt (it happens before any suspension point), so it must be at-most-
    once across attempts like any other side effect.

    Returns the entry's seq — used by ``ReActAgent._handle_message`` to give
    a TURN-stage safety middleware a stable reference back to this specific
    message (e.g. to log a companion ``user.message.flagged`` marker and,
    later, to redact this one entry from replayed context — see
    ``agents/middleware/guardrails/multimodal_safety.py`` and
    ``agents/factory.py::step_rows_from_log``).
    """
    display_text = msg.metadata.get("display_text")
    if display_text is None:
        display_text = content_blocks_to_str(user_turn.content)  # type: ignore[arg-type]
    return await ctx.log_once(
        "user.message",
        {
            "text": display_text,
            "attachments": msg.metadata.get("attachments") or [],
        },
    )


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
