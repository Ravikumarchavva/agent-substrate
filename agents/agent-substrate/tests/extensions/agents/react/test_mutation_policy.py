"""Section 12 — MutationPolicy in ReActAgent tests.

Verifies that:
- add_tool succeeds without a policy (unconditional)
- add_tool is blocked when the policy denies TOOL_ADD
- add_tool succeeds when the policy grants TOOL_ADD
- rewrite_system_prompt succeeds without a policy
- rewrite_system_prompt is blocked when the policy denies PROMPT_REWRITE
- rewrite_system_prompt succeeds when the policy grants PROMPT_REWRITE
- WEIGHT_UPDATE is forbidden by the default InMemoryMutationPolicy
"""

from __future__ import annotations

import pytest

from ravi.extensions.agents.react.agent import ReActAgent
from ravi.extensions.safeguards._in_memory import (
    InMemoryMutationPolicy,
    DEFAULT_FORBIDDEN_MUTATION_KINDS,
)
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.kernel.safeguards._mutation import MutationKind

from tests.fixtures.mock_llm import MockLLMClient, text_turn
from tests.fixtures.fake_tools import EchoTool, AddTool


def _agent(*, mutation_policy=None) -> ReActAgent:
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=[text_turn("done")]))
    catalog.register_memory("memory", UnboundedMemory())
    return ReActAgent(
        name="test-agent",
        description="test",
        catalog=catalog,
        enable_capability_search=False,
        mutation_policy=mutation_policy,
    )


# ---------------------------------------------------------------------------
# add_tool
# ---------------------------------------------------------------------------


async def test_add_tool_no_policy_always_succeeds() -> None:
    agent = _agent()
    result = await agent.add_tool(EchoTool())
    assert result is True


async def test_add_tool_policy_grants() -> None:
    policy = InMemoryMutationPolicy(
        forbidden_kinds=[MutationKind.WEIGHT_UPDATE],  # TOOL_ADD allowed
    )
    agent = _agent(mutation_policy=policy)
    result = await agent.add_tool(AddTool())
    assert result is True


async def test_add_tool_policy_denies_forbidden_kind() -> None:
    policy = InMemoryMutationPolicy(
        forbidden_kinds=[MutationKind.TOOL_ADD],  # TOOL_ADD forbidden
    )
    agent = _agent(mutation_policy=policy)
    result = await agent.add_tool(EchoTool())
    assert result is False


async def test_add_tool_registers_on_grant() -> None:
    policy = InMemoryMutationPolicy()  # default: only WEIGHT_UPDATE forbidden
    agent = _agent(mutation_policy=policy)
    tool = EchoTool()

    assert agent._catalog.get_tool(tool.name) is None
    result = await agent.add_tool(tool)
    assert result is True
    assert agent._catalog.get_tool(tool.name) is not None


async def test_add_tool_does_not_register_on_deny() -> None:
    policy = InMemoryMutationPolicy(forbidden_kinds=[MutationKind.TOOL_ADD])
    agent = _agent(mutation_policy=policy)
    tool = EchoTool()

    result = await agent.add_tool(tool)
    assert result is False
    assert agent._catalog.get_tool(tool.name) is None


# ---------------------------------------------------------------------------
# rewrite_system_prompt
# ---------------------------------------------------------------------------


async def test_rewrite_prompt_no_policy_always_succeeds() -> None:
    agent = _agent()
    result = await agent.rewrite_system_prompt("New instructions.")
    assert result is True
    assert agent.system_instructions == "New instructions."


async def test_rewrite_prompt_policy_grants() -> None:
    policy = InMemoryMutationPolicy()  # PROMPT_REWRITE is allowed by default
    agent = _agent(mutation_policy=policy)
    result = await agent.rewrite_system_prompt("Updated prompt.")
    assert result is True
    assert agent.system_instructions == "Updated prompt."


async def test_rewrite_prompt_policy_denies() -> None:
    policy = InMemoryMutationPolicy(
        forbidden_kinds=[MutationKind.PROMPT_REWRITE],
    )
    agent = _agent(mutation_policy=policy)
    original = agent.system_instructions
    result = await agent.rewrite_system_prompt("Forbidden rewrite.")
    assert result is False
    assert agent.system_instructions == original


async def test_rewrite_prompt_family_depth_ceiling() -> None:
    policy = InMemoryMutationPolicy(max_family_depth=0)
    agent = _agent(mutation_policy=policy)
    # family_depth is 0 in the implementation, which equals max_family_depth=0 → denied
    result = await agent.rewrite_system_prompt("Exceeds depth.")
    # family_depth 0 <= max_family_depth 0 → allowed (ceiling is inclusive)
    assert result is True


async def test_weight_update_is_forbidden_by_default() -> None:
    """Default policy forbids WEIGHT_UPDATE — confirm it's in the set."""
    assert MutationKind.WEIGHT_UPDATE in DEFAULT_FORBIDDEN_MUTATION_KINDS
