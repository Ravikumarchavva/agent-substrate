"""Runtime-level integration tests for dormant agent lifecycle contracts.

Core lifecycle contract tests live in tests/kernel/test_lifecycle.py.
This file tests runtime-specific behaviour: imports via ravi.kernel.runtime
and Envelope.activation field round-trip.
"""

from __future__ import annotations

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


def test_runtime_exports_lifecycle_symbols() -> None:
    """All new lifecycle names must be importable from ravi.kernel.runtime."""
    assert AgentLifecycleState is not None
    assert ActivationTrigger is not None
    assert ExecutionLease is not None
    assert CheckpointRef is not None
    assert AgentActivationContract is not None
    assert Checkpointable is not None
    assert ActivationAware is not None


def test_execution_lease_immutable() -> None:
    lease = ExecutionLease(
        agent_id_str="agent/a/default",
        worker_id="pod-1",
        lease_id="lease-1",
        granted_at="2026-05-24T10:00:00Z",
        expires_at="2026-05-24T10:00:30Z",
    )
    with pytest.raises((AttributeError, TypeError)):
        lease.worker_id = "mutated"  # type: ignore[misc]


def test_checkpoint_ref_immutable() -> None:
    ref = CheckpointRef(
        agent_id_str="agent/b/default",
        checkpoint_id="ck-001",
        sequence=1,
        store_uri="redis://localhost/ck-001",
    )
    with pytest.raises((AttributeError, TypeError)):
        ref.checkpoint_id = "mutated"  # type: ignore[misc]


def test_envelope_activation_field_round_trip() -> None:
    from ravi.kernel.runtime._identity import AgentId

    contract = AgentActivationContract(
        lifecycle_state=AgentLifecycleState.ACTIVE,
    )
    env = Envelope(
        sender=None,
        target=AgentId("target", "default"),
        content=[],
        activation=contract,
    )
    assert env.activation is contract
    assert env.activation.lifecycle_state is AgentLifecycleState.ACTIVE


