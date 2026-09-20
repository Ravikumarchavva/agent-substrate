"""Tests for Phase 1B: HistoryResolver + ContextBuilder Integration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from substrate.agents.context.builder import DefaultContextBuilder
from substrate.agents.context.history import DefaultHistoryResolver, InMemoryHistoryProvider
from substrate.kernel.agent.context import ContextWindow
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.storage.history import MessageNode


def _msg(text: str, role: str = "user") -> ChatMessage:
    return ChatMessage(role=role, content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# 1. ContextWindow Immutability & Contracts
# ---------------------------------------------------------------------------


def test_context_window_immutability():
    cw = ContextWindow(
        messages=[_msg("hi")],
        leaf_node_id="n1",
        estimated_tokens=5,
    )
    with pytest.raises(ValidationError):
        cw.estimated_tokens = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. DefaultContextBuilder Behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_builder_empty_nodes():
    builder = DefaultContextBuilder()
    window = await builder.build([])
    assert window.messages == []
    assert window.leaf_node_id is None
    assert window.estimated_tokens == 0


@pytest.mark.asyncio
async def test_context_builder_prepends_system_instruction():
    builder = DefaultContextBuilder()
    nodes = [
        MessageNode(id="n1", session_id="s1", payload=_msg("User question")),
    ]
    window = await builder.build(nodes, system_instruction="You are a helpful assistant.")
    assert len(window.messages) == 2
    assert window.messages[0].role == Role.SYSTEM
    assert window.messages[0].text == "You are a helpful assistant."
    assert window.messages[1].text == "User question"
    assert window.leaf_node_id == "n1"


@pytest.mark.asyncio
async def test_context_builder_avoids_duplicate_system_instruction():
    builder = DefaultContextBuilder()
    nodes = [
        MessageNode(id="n0", session_id="s1", payload=_msg("Existing system", role=Role.SYSTEM)),
        MessageNode(id="n1", parent_id="n0", session_id="s1", payload=_msg("User question")),
    ]
    window = await builder.build(nodes, system_instruction="New system")
    # Does not duplicate system prompt if one is already at index 0
    assert len(window.messages) == 2
    assert window.messages[0].text == "Existing system"


@pytest.mark.asyncio
async def test_context_builder_sliding_window_token_budget():
    builder = DefaultContextBuilder()
    nodes = [
        MessageNode(id="n1", session_id="s1", payload=_msg("Message 1: Very long text with lots of details")),
        MessageNode(id="n2", parent_id="n1", session_id="s1", payload=_msg("Message 2: Another detailed turn")),
        MessageNode(id="n3", parent_id="n2", session_id="s1", payload=_msg("Message 3: Recent turn")),
    ]

    # Without budget: keeps all
    full_window = await builder.build(nodes, system_instruction="System prompt")
    assert len(full_window.messages) == 4

    # With tight budget: preserves system prompt + drops oldest turns
    tight_budget = 20  # Tokens
    trimmed_window = await builder.build(
        nodes, token_budget=tight_budget, system_instruction="System prompt"
    )
    assert trimmed_window.messages[0].role == Role.SYSTEM
    assert trimmed_window.messages[-1].text == "Message 3: Recent turn"
    assert len(trimmed_window.messages) < len(full_window.messages)
    assert trimmed_window.estimated_tokens <= tight_budget


# ---------------------------------------------------------------------------
# 3. End-to-End: HistoryProvider -> HistoryResolver -> ContextBuilder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_ancestry_to_prompt_window():
    provider = InMemoryHistoryProvider()

    # Turn 0
    n0 = MessageNode(id="n0", session_id="s1", parent_id=None, payload=_msg("What is AI?"))
    await provider.append_and_advance(n0, "main")

    # Turn 1
    n1 = MessageNode(
        id="n1", session_id="s1", parent_id="n0", payload=_msg("AI is intelligence demonstrated by machines", role="assistant")
    )
    await provider.append_and_advance(n1, "main")

    # Turn 2
    n2 = MessageNode(
        id="n2", session_id="s1", parent_id="n1", payload=_msg("Can it learn?")
    )
    b2 = await provider.append_and_advance(n2, "main")

    # 1. Resolve DAG ancestry for current head
    resolver = DefaultHistoryResolver(provider)
    ancestry_nodes = await resolver.resolve_ancestry(b2.head_message_id)
    assert [n.id for n in ancestry_nodes] == ["n0", "n1", "n2"]

    # 2. Build ContextWindow with ContextBuilder
    builder = DefaultContextBuilder()
    window = await builder.build(
        ancestry_nodes,
        system_instruction="Be concise.",
    )

    assert len(window.messages) == 4
    assert window.messages[0].role == Role.SYSTEM
    assert window.messages[1].text == "What is AI?"
    assert window.messages[2].text == "AI is intelligence demonstrated by machines"
    assert window.messages[3].text == "Can it learn?"
    assert window.leaf_node_id == "n2"
    assert window.estimated_tokens > 0

