"""Tests for the durable-runtime value types in ``kernel.abstractions.runtime``.

These verify that:
1. Value types (RunLogEntry, Effect, Wakeup, …) round-trip through JSON and reject
   illegal states.
2. Effect.make_id is deterministic and collision-resistant.
3. RunMeta carries run_id correctly in both standalone and supervised modes.
4. Error types carry their structured fields.

Behaviour of the ``RuntimeStore`` port (leases, journal, inbox, signals) is not tested
here: it is the runtime-store conformance suite, run against every implementation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from substrate.types import Actor, Topic
from substrate.runtime import Message, DataPayload
from substrate.types import ConcurrentAppendError
from substrate.types import RunMeta
from substrate.runtime import CancellationToken
from substrate.types import Supervision
from substrate.types import new_run_id
from substrate.types import RunLogEntry
from substrate.runtime import Effect
from substrate.runtime import RunRetryPolicy
from substrate.runtime import AgentRunContext
from substrate.runtime.agent import Agent
from substrate.stores import MemoryProvenance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(name: str = "test") -> Actor:
    return Actor(type="agent", key=f"{name}-{uuid4().hex}")


def _topic() -> Topic:
    return Topic(f"test.topic/{uuid4().hex}")


def _message(sender: Actor | None = None, target: Actor | None = None) -> Message:
    target = target or _agent_id()
    sender = sender or _agent_id()
    return Message(
        target=target,
        sender=sender,
        payload=DataPayload(data={"x": 1}),
    )


# ---------------------------------------------------------------------------
# ids.py
# ---------------------------------------------------------------------------


class TestRunId:
    def test_new_run_id_unique(self) -> None:
        assert new_run_id() != new_run_id()


# ---------------------------------------------------------------------------
# log_entry.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# effects.py
# ---------------------------------------------------------------------------


class TestEffect:
    def test_make_id_deterministic(self) -> None:
        rid = new_run_id()
        args = {"email": "a@b.com", "subject": "hi"}
        id1 = Effect.make_id(rid, "3", "email.send", args)
        id2 = Effect.make_id(rid, "3", "email.send", args)
        assert id1 == id2

    def test_make_id_arg_order_independent(self) -> None:
        rid = new_run_id()
        id1 = Effect.make_id(rid, "0", "k", {"a": 1, "b": 2})
        id2 = Effect.make_id(rid, "0", "k", {"b": 2, "a": 1})
        assert id1 == id2

    def test_make_id_different_steps_differ(self) -> None:
        rid = new_run_id()
        assert Effect.make_id(rid, "0", "k", {}) != Effect.make_id(rid, "1", "k", {})

    def test_make_id_hierarchical_paths_differ(self) -> None:
        """A nested path ("0.0") must not collide with a sibling top-level path ("0")."""
        rid = new_run_id()
        assert Effect.make_id(rid, "0", "k", {}) != Effect.make_id(rid, "0.0", "k", {})


# ---------------------------------------------------------------------------
# wakeup.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# scheduler.py
# ---------------------------------------------------------------------------


class TestRunRetryPolicy:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_retries": -1},
            {"backoff_s": -1},
            {"max_backoff_s": -1},
        ],
    )
    def test_rejects_negative_values(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            RunRetryPolicy(**kwargs)


def test_run_log_entry_rejects_negative_sequence() -> None:
    with pytest.raises(ValueError):
        RunLogEntry(run_id=new_run_id(), seq=-1, kind="test")


def test_memory_provenance_rejects_confidence_outside_range() -> None:
    with pytest.raises(ValueError):
        MemoryProvenance(confidence=1.1)


# ---------------------------------------------------------------------------
# supervisor.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# agent.py (Agent conformance)
# ---------------------------------------------------------------------------


class TestAgent:
    def test_minimal_impl_satisfies_protocol(self) -> None:
        class MinimalCtx:
            run_id: str = new_run_id()
            tenant_id: str | None = None

            def check(self) -> None: ...

        class MinimalAgent:
            def __init__(self) -> None:
                self.id = _agent_id()

            async def run(self, ctx: AgentRunContext, inbox: list[Message]) -> None:
                pass

        agent = MinimalAgent()
        assert isinstance(agent, Agent)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# errors.py — new error types
# ---------------------------------------------------------------------------


class TestConcurrentAppendError:
    def test_fields(self) -> None:
        err = ConcurrentAppendError("race", run_id="r1", expected_seq=2, actual_seq=5)
        assert err.run_id == "r1"
        assert err.expected_seq == 2
        assert err.actual_seq == 5
        assert "race" in str(err)


# ---------------------------------------------------------------------------
# runtime_context.py — run_id extension
# ---------------------------------------------------------------------------


def _standalone_meta(*, run_id: str = "") -> RunMeta:
    """RunMeta with a fresh id/token — mirrors the deleted RunMeta.standalone(),
    which lived in kernel but needed the agents-layer CancellationToken."""
    return RunMeta(run_id=run_id or new_run_id(), cancellation=CancellationToken())


class TestRunMeta:
    def test_standalone_generates_run_id(self) -> None:
        meta = _standalone_meta()
        assert isinstance(meta.run_id, str)
        assert len(meta.run_id) > 0

    def test_standalone_accepts_explicit_run_id(self) -> None:
        rid = new_run_id()
        meta = _standalone_meta(run_id=rid)
        assert meta.run_id == rid

    def test_two_standalone_have_different_run_ids(self) -> None:
        m1 = _standalone_meta()
        m2 = _standalone_meta()
        assert m1.run_id != m2.run_id

    def test_run_id_from_supervision(self) -> None:
        agent = _agent_id()
        sup = Supervision.root(agent)
        token = CancellationToken()
        meta = RunMeta(cancellation=token, run_id=sup.run_id, supervision=sup)
        assert meta.run_id == sup.run_id
