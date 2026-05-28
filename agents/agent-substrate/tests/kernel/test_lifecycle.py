"""Tests for dormant agent lifecycle kernel contracts (new model)."""

from __future__ import annotations

from typing import Any

import pytest

from ravi.kernel.runtime import (
    ActivationAware,
    ActivationTrigger,
    AgentActivationContract,
    AgentLifecycleState,
    Checkpointable,
    CheckpointRef,
    Envelope,
    ExecutionLease,
)
from ravi.kernel.runtime._identity import AgentId


# ---------------------------------------------------------------------------
# 1. AgentLifecycleState has all 7 states
# ---------------------------------------------------------------------------


def test_lifecycle_state_has_all_7_states() -> None:
    expected = {
        "DORMANT",
        "ACTIVATING",
        "ACTIVE",
        "CHECKPOINTING",
        "HIBERNATING",
        "SUSPENDED",
        "TERMINATED",
    }
    assert {s.name for s in AgentLifecycleState} == expected


# ---------------------------------------------------------------------------
# 2. ActivationTrigger with replayed=True sets the flag
# ---------------------------------------------------------------------------


def test_activation_trigger_replayed_flag() -> None:
    trigger = ActivationTrigger(
        trigger_type="replay",
        source_id="evt-001",
        replayed=True,
    )
    assert trigger.replayed is True
    assert trigger.trigger_type == "replay"
    assert trigger.source_id == "evt-001"


def test_activation_trigger_replayed_default_false() -> None:
    trigger = ActivationTrigger(trigger_type="message", source_id="env-abc")
    assert trigger.replayed is False
    assert trigger.wakeup_key is None


# ---------------------------------------------------------------------------
# 3. ExecutionLease is frozen/immutable
# ---------------------------------------------------------------------------


def test_execution_lease_frozen() -> None:
    lease = ExecutionLease(
        agent_id_str="agent/worker/default",
        worker_id="pod-1",
        lease_id="lease-xyz",
        granted_at="2026-05-24T10:00:00Z",
        expires_at="2026-05-24T10:00:30Z",
        budget_tokens=4096,
        budget_steps=50,
    )
    with pytest.raises((AttributeError, TypeError)):
        lease.worker_id = "mutated"  # type: ignore[misc]


def test_execution_lease_construction() -> None:
    lease = ExecutionLease(
        agent_id_str="agent/planner/default",
        worker_id="pod-2",
        lease_id="lease-001",
        granted_at="2026-05-24T12:00:00Z",
        expires_at="2026-05-24T12:00:30Z",
    )
    assert lease.budget_tokens == 0
    assert lease.budget_steps == 0


# ---------------------------------------------------------------------------
# 4. AgentActivationContract with all fields populated
# ---------------------------------------------------------------------------


def test_agent_activation_contract_fully_populated() -> None:
    trigger = ActivationTrigger(trigger_type="message", source_id="env-999")
    lease = ExecutionLease(
        agent_id_str="agent/x/default",
        worker_id="pod-5",
        lease_id="l-1",
        granted_at="2026-05-24T00:00:00Z",
        expires_at="2026-05-24T00:00:30Z",
    )
    ckpt = CheckpointRef(
        agent_id_str="agent/x/default",
        checkpoint_id="deadbeef",
        sequence=7,
        store_uri="s3://bucket/agent/x",
        byte_size=1024,
        created_at="2026-05-23T23:00:00Z",
    )
    contract = AgentActivationContract(
        lifecycle_state=AgentLifecycleState.ACTIVE,
        trigger=trigger,
        lease=lease,
        last_checkpoint=ckpt,
        depth=2,
        max_depth=16,
    )
    assert contract.lifecycle_state is AgentLifecycleState.ACTIVE
    assert contract.trigger is trigger
    assert contract.lease is lease
    assert contract.last_checkpoint is ckpt
    assert contract.depth == 2
    assert contract.max_depth == 16


# ---------------------------------------------------------------------------
# 5. AgentActivationContract default depth=0, max_depth=32
# ---------------------------------------------------------------------------


def test_agent_activation_contract_defaults() -> None:
    contract = AgentActivationContract(
        lifecycle_state=AgentLifecycleState.DORMANT,
    )
    assert contract.depth == 0
    assert contract.max_depth == 32
    assert contract.trigger is None
    assert contract.lease is None
    assert contract.last_checkpoint is None


# ---------------------------------------------------------------------------
# 6. Envelope with activation=AgentActivationContract(...) stores it
# ---------------------------------------------------------------------------


def test_envelope_stores_activation_contract() -> None:
    contract = AgentActivationContract(
        lifecycle_state=AgentLifecycleState.ACTIVATING,
        depth=1,
    )
    sender = AgentId("sender", "default")
    target = AgentId("worker", "default")
    env = Envelope(
        sender=sender,
        target=target,
        content=[],
        activation=contract,
    )
    assert env.activation is contract
    assert env.activation.lifecycle_state is AgentLifecycleState.ACTIVATING
    assert env.activation.depth == 1


def test_envelope_activation_defaults_to_none() -> None:
    env = Envelope(
        sender=None,
        target=AgentId("agent", "default"),
        content=[],
    )
    assert env.activation is None


# ---------------------------------------------------------------------------
# 7. A class implementing Checkpointable is recognized by isinstance
# ---------------------------------------------------------------------------


class _MyCheckpointable:
    async def to_checkpoint(self) -> dict[str, Any]:
        return {"state": "ok"}

    @classmethod
    async def from_checkpoint(cls, data: dict[str, Any]) -> "_MyCheckpointable":
        return cls()


def test_checkpointable_isinstance() -> None:
    obj = _MyCheckpointable()
    assert isinstance(obj, Checkpointable)


def test_non_checkpointable_isinstance() -> None:
    class _Missing:
        async def to_checkpoint(self) -> dict[str, Any]:
            return {}
        # missing from_checkpoint

    assert not isinstance(_Missing(), Checkpointable)


# ---------------------------------------------------------------------------
# 8. A class implementing ActivationAware is recognized by isinstance
# ---------------------------------------------------------------------------


class _MyActivationAware:
    async def on_activating(self, contract: AgentActivationContract) -> None:
        pass

    async def on_hibernating(self, contract: AgentActivationContract) -> None:
        pass

    async def on_suspended(self, contract: AgentActivationContract) -> None:
        pass


def test_activation_aware_isinstance() -> None:
    obj = _MyActivationAware()
    assert isinstance(obj, ActivationAware)


def test_non_activation_aware_isinstance() -> None:
    class _Partial:
        async def on_activating(self, contract: AgentActivationContract) -> None:
            pass
        # missing on_hibernating and on_suspended

    assert not isinstance(_Partial(), ActivationAware)
