"""Tests for the redesigned principal identity system."""

from __future__ import annotations

import pytest

from ravi.kernel.runtime._identity import (
    AgentId,
    DelegationToken,
    IdentityContext,
    PrincipalId,
    PrincipalKind,
)
from ravi.kernel.runtime._contracts import Envelope


# ---------------------------------------------------------------------------
# AgentId
# ---------------------------------------------------------------------------


class TestAgentIdGenerate:
    def test_generate_returns_agent_id(self) -> None:
        aid = AgentId.generate("react_agent")
        assert aid.type == "react_agent"
        assert isinstance(aid.key, str)
        assert len(aid.key) == 32  # uuid4().hex

    def test_generate_produces_unique_keys(self) -> None:
        a = AgentId.generate("bot")
        b = AgentId.generate("bot")
        assert a.key != b.key

    def test_str_format(self) -> None:
        aid = AgentId(type="planner", key="abc123")
        assert str(aid) == "planner/abc123"


# ---------------------------------------------------------------------------
# PrincipalId — fqn
# ---------------------------------------------------------------------------


class TestPrincipalIdFqn:
    def test_fqn_format(self) -> None:
        p = PrincipalId(
            kind=PrincipalKind.AGENT,
            tenant_id="acme",
            workspace_id="ws1",
            name="planner",
        )
        assert p.fqn == "agent/acme/ws1/planner"

    def test_fqn_uses_lowercase_kind_name(self) -> None:
        for kind in PrincipalKind:
            p = PrincipalId(kind=kind, tenant_id="t", workspace_id="w", name="x")
            assert p.fqn.startswith(kind.name.lower() + "/")

    def test_fqn_contains_all_segments(self) -> None:
        p = PrincipalId(
            kind=PrincipalKind.TOOL,
            tenant_id="tenant1",
            workspace_id="workspace1",
            name="search_tool",
        )
        parts = p.fqn.split("/")
        assert parts == ["tool", "tenant1", "workspace1", "search_tool"]


# ---------------------------------------------------------------------------
# PrincipalId — fingerprint
# ---------------------------------------------------------------------------


class TestPrincipalIdFingerprint:
    def test_fingerprint_is_16_chars(self) -> None:
        p = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot")
        assert len(p.fingerprint) == 16

    def test_fingerprint_is_hex(self) -> None:
        p = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot")
        int(p.fingerprint, 16)  # raises ValueError if not hex

    def test_fingerprint_is_deterministic(self) -> None:
        uid = "fixed-uid-for-test"
        p1 = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot", uid=uid)
        p2 = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot", uid=uid)
        assert p1.fingerprint == p2.fingerprint

    def test_fingerprint_differs_by_kind(self) -> None:
        uid = "same-uid"
        p1 = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="x", uid=uid)
        p2 = PrincipalId(kind=PrincipalKind.TOOL, tenant_id="t", workspace_id="w", name="x", uid=uid)
        assert p1.fingerprint != p2.fingerprint

    def test_fingerprint_differs_by_uid(self) -> None:
        p1 = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot", uid="uid-a")
        p2 = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot", uid="uid-b")
        assert p1.fingerprint != p2.fingerprint


# ---------------------------------------------------------------------------
# PrincipalId — as_agent_id
# ---------------------------------------------------------------------------


class TestPrincipalIdAsAgentId:
    def test_as_agent_id_type_is_lowercase_kind_name(self) -> None:
        p = PrincipalId(kind=PrincipalKind.WORKFLOW, tenant_id="t", workspace_id="w", name="pipe")
        aid = p.as_agent_id()
        assert aid.type == "workflow"

    def test_as_agent_id_key_is_uid(self) -> None:
        uid = "deadbeef" * 4
        p = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="bot", uid=uid)
        aid = p.as_agent_id()
        assert aid.key == uid

    def test_as_agent_id_returns_agent_id(self) -> None:
        p = PrincipalId(kind=PrincipalKind.HUMAN, tenant_id="t", workspace_id="w", name="alice")
        aid = p.as_agent_id()
        assert isinstance(aid, AgentId)


# ---------------------------------------------------------------------------
# DelegationToken — is_expired
# ---------------------------------------------------------------------------


class TestDelegationTokenIsExpired:
    @pytest.fixture()
    def principals(self) -> tuple[PrincipalId, PrincipalId]:
        p1 = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="delegator")
        p2 = PrincipalId(kind=PrincipalKind.TOOL, tenant_id="t", workspace_id="w", name="delegate")
        return p1, p2

    def test_none_expiry_never_expires(self, principals: tuple[PrincipalId, PrincipalId]) -> None:
        p1, p2 = principals
        token = DelegationToken(delegator=p1, delegate=p2, scopes=("tool:execute",))
        assert token.is_expired("9999-12-31T23:59:59") is False

    def test_past_expiry_returns_true(self, principals: tuple[PrincipalId, PrincipalId]) -> None:
        p1, p2 = principals
        token = DelegationToken(
            delegator=p1,
            delegate=p2,
            scopes=(),
            expires_at="2000-01-01T00:00:00",
        )
        assert token.is_expired("2025-01-01T00:00:00") is True

    def test_future_expiry_returns_false(self, principals: tuple[PrincipalId, PrincipalId]) -> None:
        p1, p2 = principals
        token = DelegationToken(
            delegator=p1,
            delegate=p2,
            scopes=(),
            expires_at="9999-01-01T00:00:00",
        )
        assert token.is_expired("2025-01-01T00:00:00") is False

    def test_equal_timestamps_not_expired(self, principals: tuple[PrincipalId, PrincipalId]) -> None:
        p1, p2 = principals
        ts = "2025-06-01T12:00:00"
        token = DelegationToken(delegator=p1, delegate=p2, scopes=(), expires_at=ts)
        # now_iso == expires_at → not expired (strictly greater required)
        assert token.is_expired(ts) is False


# ---------------------------------------------------------------------------
# IdentityContext — with_delegation
# ---------------------------------------------------------------------------


class TestIdentityContextWithDelegation:
    @pytest.fixture()
    def setup(self) -> tuple[PrincipalId, PrincipalId, PrincipalId]:
        orchestrator = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="orch")
        worker = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="worker")
        tool = PrincipalId(kind=PrincipalKind.TOOL, tenant_id="t", workspace_id="w", name="search")
        return orchestrator, worker, tool

    def test_initial_chain_is_empty(self, setup: tuple[PrincipalId, PrincipalId, PrincipalId]) -> None:
        orchestrator, _, _ = setup
        ctx = IdentityContext(principal=orchestrator)
        assert ctx.delegation_chain == ()

    def test_with_delegation_appends_token(self, setup: tuple[PrincipalId, PrincipalId, PrincipalId]) -> None:
        orchestrator, worker, _ = setup
        ctx = IdentityContext(principal=orchestrator)
        token = DelegationToken(delegator=orchestrator, delegate=worker, scopes=("memory:read",))
        ctx2 = ctx.with_delegation(token)
        assert len(ctx2.delegation_chain) == 1
        assert ctx2.delegation_chain[0] is token

    def test_with_delegation_is_immutable(self, setup: tuple[PrincipalId, PrincipalId, PrincipalId]) -> None:
        orchestrator, worker, _ = setup
        ctx = IdentityContext(principal=orchestrator)
        token = DelegationToken(delegator=orchestrator, delegate=worker, scopes=())
        ctx2 = ctx.with_delegation(token)
        # original is unchanged
        assert ctx.delegation_chain == ()
        assert len(ctx2.delegation_chain) == 1

    def test_with_delegation_chain_grows(self, setup: tuple[PrincipalId, PrincipalId, PrincipalId]) -> None:
        orchestrator, worker, tool = setup
        ctx = IdentityContext(principal=orchestrator)
        t1 = DelegationToken(delegator=orchestrator, delegate=worker, scopes=("memory:read",))
        t2 = DelegationToken(delegator=worker, delegate=tool, scopes=("tool:execute",))
        ctx2 = ctx.with_delegation(t1).with_delegation(t2)
        assert len(ctx2.delegation_chain) == 2
        assert ctx2.delegation_chain[0] is t1
        assert ctx2.delegation_chain[1] is t2

    def test_effective_tenant_id(self, setup: tuple[PrincipalId, PrincipalId, PrincipalId]) -> None:
        orchestrator, _, _ = setup
        ctx = IdentityContext(principal=orchestrator)
        assert ctx.effective_tenant_id == "t"

    def test_effective_workspace_id(self, setup: tuple[PrincipalId, PrincipalId, PrincipalId]) -> None:
        orchestrator, _, _ = setup
        ctx = IdentityContext(principal=orchestrator)
        assert ctx.effective_workspace_id == "w"


# ---------------------------------------------------------------------------
# Envelope — accepts identity
# ---------------------------------------------------------------------------


class TestEnvelopeIdentity:
    def test_envelope_default_identity_is_none(self) -> None:
        sender = AgentId(type="agent", key="sender")
        target = AgentId(type="agent", key="target")
        env = Envelope(sender=sender, target=target, content=[])
        assert env.identity is None

    def test_envelope_stores_identity_context(self) -> None:
        sender = AgentId(type="agent", key="sender")
        target = AgentId(type="agent", key="target")
        p = PrincipalId(
            kind=PrincipalKind.AGENT,
            tenant_id="acme",
            workspace_id="ws",
            name="orchestrator",
        )
        ctx = IdentityContext(principal=p)
        env = Envelope(sender=sender, target=target, content=[], identity=ctx)
        assert env.identity is ctx
        assert env.identity.principal is p

    def test_envelope_identity_chain_accessible(self) -> None:
        sender = AgentId(type="agent", key="s")
        target = AgentId(type="agent", key="t")
        delegator = PrincipalId(kind=PrincipalKind.AGENT, tenant_id="t", workspace_id="w", name="orch")
        delegate = PrincipalId(kind=PrincipalKind.TOOL, tenant_id="t", workspace_id="w", name="search")
        token = DelegationToken(delegator=delegator, delegate=delegate, scopes=("tool:execute",))
        ctx = IdentityContext(principal=delegator).with_delegation(token)
        env = Envelope(sender=sender, target=target, content=[], identity=ctx)
        assert len(env.identity.delegation_chain) == 1  # type: ignore[union-attr]
