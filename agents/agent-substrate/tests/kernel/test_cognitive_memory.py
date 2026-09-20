"""Unit tests for cognitive memory architecture kernel contracts.

Verifies:
- MemoryRecord immutability and content normalization
- MemoryRecord.candidate() vs from_text() status separation
- MemoryNamespace construction, from_actor(), and exact query semantics
- MemoryMatch properties and forwardings
- MemoryProvenance lineage (supersedes_id, branch_id)
- MemoryValidity valid-time fields
- MemoryQuery specification defaults
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from substrate.kernel.core.content import DataBlock, MediaBlock, TextBlock
from substrate.kernel.core.identity import Actor
from substrate.kernel.storage.memory import (
    ContextMemoryInjection,
    MemoryCategory,
    MemoryLifecycle,
    MemoryMatch,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    MemoryValidity,
)


def test_memory_record_content_normalization():
    # String content is normalized to tuple of TextBlock
    r1 = MemoryRecord(content="User prefers Python 3.13")
    assert isinstance(r1.content, tuple)
    assert len(r1.content) == 1
    assert isinstance(r1.content[0], TextBlock)
    assert r1.content[0].text == "User prefers Python 3.13"
    assert r1.to_text() == "User prefers Python 3.13"

    # Single ContentBlock is normalized to 1-tuple
    block = DataBlock(data={"key": "val"})
    r2 = MemoryRecord(content=block)
    assert isinstance(r2.content, tuple)
    assert len(r2.content) == 1
    assert r2.content[0] is block

    # List of blocks is normalized to tuple
    blocks = [TextBlock(text="Note"), MediaBlock.image(url="https://example.com/a.png")]
    r3 = MemoryRecord(content=blocks)
    assert isinstance(r3.content, tuple)
    assert len(r3.content) == 2


def test_candidate_vs_active_construction():
    # Manual from_text defaults to ACTIVE
    active = MemoryRecord.from_text("Always use dark mode", category=MemoryCategory.DIRECTIVE)
    assert active.status == MemoryStatus.ACTIVE
    assert active.category == MemoryCategory.DIRECTIVE

    # Automatic extraction via candidate() creates CANDIDATE
    cand = MemoryRecord.candidate(
        "Observation from web search",
        category=MemoryCategory.EPISODIC,
        provenance=MemoryProvenance(
            source_session_id="sess-123",
            source_branch_id="branch-exp",
            extraction_method="llm_reflection",
            confidence=0.85,
        ),
    )
    assert cand.status == MemoryStatus.CANDIDATE
    assert cand.category == MemoryCategory.EPISODIC
    assert cand.provenance.source_branch_id == "branch-exp"
    assert cand.provenance.extraction_method == "llm_reflection"
    assert cand.provenance.confidence == 0.85


def test_memory_provenance_supersedes_lineage():
    old_id = "mem-old-123"
    new_mem = MemoryRecord.from_text(
        "User updated to Python 3.13",
        provenance=MemoryProvenance(
            supersedes_id=old_id,
            extraction_method="user_correction",
        ),
    )
    assert new_mem.provenance.supersedes_id == old_id
    assert new_mem.provenance.extraction_method == "user_correction"


def test_memory_namespace_from_actor():
    user = Actor(type="user", key="user-42")
    ns_user = MemoryNamespace.from_actor(user, tenant_id="acme", session_id="sess-1")
    assert ns_user.tenant_id == "acme"
    assert ns_user.user_id == "user-42"
    assert ns_user.agent_id is None
    assert ns_user.session_id == "sess-1"

    agent = Actor(type="agent", key="code-assistant")
    ns_agent = MemoryNamespace.from_actor(agent, tenant_id="acme")
    assert ns_agent.tenant_id == "acme"
    assert ns_agent.agent_id == "code-assistant"
    assert ns_agent.user_id is None


def test_memory_validity_temporal_fields():
    t_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t_end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    validity = MemoryValidity(valid_from=t_start, valid_until=t_end)
    rec = MemoryRecord.from_text("Worked on Q1 Initiative", validity=validity)
    assert rec.validity.valid_from == t_start
    assert rec.validity.valid_until == t_end


def test_memory_match_forwarding_properties():
    rec = MemoryRecord.from_text(
        "TypeScript with strictNullChecks",
        category=MemoryCategory.DIRECTIVE,
        metadata={"priority": "high"},
    )
    match = MemoryMatch(record=rec, score=0.94, rank=1, retrieval_method="hybrid")

    assert match.score == 0.94
    assert match.rank == 1
    assert match.retrieval_method == "hybrid"
    # Forwarding properties match underlying record
    assert match.id == rec.id
    assert match.category == MemoryCategory.DIRECTIVE
    assert match.metadata["priority"] == "high"
    assert match.to_text() == "TypeScript with strictNullChecks"
    assert str(match) == "TypeScript with strictNullChecks"


def test_memory_query_defaults():
    ns = MemoryNamespace(tenant_id="tenant-1")
    query = MemoryQuery(namespace=ns, text_query="authentication")
    assert query.namespace.tenant_id == "tenant-1"
    assert query.statuses == (MemoryStatus.ACTIVE,)
    assert query.limit == 10
    assert query.min_score == 0.0
    assert query.include_pinned is True


def test_context_memory_injection_contract():
    dir_block = TextBlock(text="Directive: concise")
    mem_block = TextBlock(text="Past discussion on Redis")
    inj = ContextMemoryInjection(
        directives=[dir_block],
        relevant_memories=[mem_block],
        estimated_tokens=45,
    )
    assert len(inj.directives) == 1
    assert len(inj.relevant_memories) == 1
    assert inj.estimated_tokens == 45

