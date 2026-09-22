"""Unit tests for MemoryExposurePolicy and DefaultContextBuilder memory injection."""

from __future__ import annotations

import pytest

from substrate.agents.context.builder import DefaultContextBuilder
from substrate.integrations.memory.policy import DefaultMemoryExposurePolicy
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.storage.history import MessageNode
from substrate.kernel.storage.memory import (
    MemoryCategory,
    MemoryMatch,
    MemoryNamespace,
    MemoryRecord,
)


@pytest.fixture
def policy() -> DefaultMemoryExposurePolicy:
    return DefaultMemoryExposurePolicy(chars_per_token=4.0, directive_ratio=0.4)


@pytest.fixture
def builder() -> DefaultContextBuilder:
    return DefaultContextBuilder(chars_per_token=4.0)


async def test_policy_budget_allocation_and_tagging(policy: DefaultMemoryExposurePolicy) -> None:
    ns = MemoryNamespace(tenant_id="test-tenant", user_id="user-1")

    directives = [
        MemoryRecord.from_text("Always reply concisely.", category=MemoryCategory.DIRECTIVE, namespace=ns),
        MemoryRecord.from_text("Prefer Python 3.13 syntax.", category=MemoryCategory.DIRECTIVE, namespace=ns),
    ]

    fact1 = MemoryRecord.from_text("User is working on Substrate framework.", category=MemoryCategory.SEMANTIC, namespace=ns)
    fact2 = MemoryRecord.from_text("User deployed SeaweedFS at port 9101.", category=MemoryCategory.SEMANTIC, namespace=ns)

    matches = [
        MemoryMatch(record=fact1, score=0.92, rank=1, retrieval_method="hybrid"),
        MemoryMatch(record=fact2, score=0.81, rank=2, retrieval_method="hybrid"),
    ]

    injection = await policy.select_for_context(directives, matches, token_budget=500)

    assert len(injection.directives) == 1
    directives_text = injection.directives[0].text
    assert "<user_preferences>" in directives_text
    assert "</user_preferences>" in directives_text
    assert "Always reply concisely." in directives_text
    assert "Prefer Python 3.13 syntax." in directives_text
    assert "<!-- Background user preferences" in directives_text

    assert len(injection.relevant_memories) == 1
    memories_text = injection.relevant_memories[0].text
    assert "<relevant_memories>" in memories_text
    assert "</relevant_memories>" in memories_text
    assert "Substrate framework" in memories_text
    assert "SeaweedFS" in memories_text
    assert injection.estimated_tokens > 0


async def test_policy_xml_escaping(policy: DefaultMemoryExposurePolicy) -> None:
    ns = MemoryNamespace(tenant_id="test-tenant", user_id="user-1")
    directives = [
        MemoryRecord.from_text('User says: if x < 10 && y > 20 then "ok"', category=MemoryCategory.DIRECTIVE, namespace=ns),
    ]
    injection = await policy.select_for_context(directives, [], token_budget=500)
    text = injection.directives[0].text
    assert "&lt; 10 &amp;&amp; y &gt; 20" in text
    assert "&quot;ok&quot;" in text


async def test_context_builder_with_memory_injection(
    policy: DefaultMemoryExposurePolicy,
    builder: DefaultContextBuilder,
) -> None:
    ns = MemoryNamespace(tenant_id="test-tenant", user_id="user-1")
    directives = [MemoryRecord.from_text("Direct concise replies.", category=MemoryCategory.DIRECTIVE, namespace=ns)]
    fact = MemoryRecord.from_text("Active project is Agent Substrate.", category=MemoryCategory.SEMANTIC, namespace=ns)
    matches = [MemoryMatch(record=fact, score=0.9, rank=1)]

    injection = await policy.select_for_context(directives, matches, token_budget=500)

    nodes = [
        MessageNode(
            id="n1",
            session_id="s1",
            payload=ChatMessage(role=Role.USER, content=[TextBlock(text="What is the current project?")]),
        ),
    ]

    window = await builder.build(
        nodes,
        system_instruction="You are a helpful coding assistant.",
        memory_injection=injection,
    )

    assert len(window.messages) == 3
    # 1. System prompt with directives appended
    sys_msg = window.messages[0]
    assert sys_msg.role == Role.SYSTEM
    assert "You are a helpful coding assistant." in sys_msg.text
    assert "<user_preferences>" in sys_msg.text
    assert "Direct concise replies." in sys_msg.text

    # 2. Relevant memories context block
    mem_msg = window.messages[1]
    assert mem_msg.role == Role.USER
    assert mem_msg.metadata.get("is_memory_context") is True
    assert "<relevant_memories>" in mem_msg.text
    assert "Active project is Agent Substrate." in mem_msg.text

    # 3. User message
    user_msg = window.messages[2]
    assert user_msg.role == Role.USER
    assert "What is the current project?" in user_msg.text
