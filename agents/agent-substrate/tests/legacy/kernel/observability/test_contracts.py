"""Kernel observability contract value-object tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ravi.kernel.messages.content import TextBlock
from ravi.kernel.observability import (
    EnvelopeSpan,
    KillSwitchRule,
    KillSwitchScope,
    KillSwitchTarget,
    ReplayDenyRule,
    ReplayRequest,
    SpanQuery,
    SpanStatus,
)
from ravi.kernel.runtime._contracts import Envelope
from ravi.kernel.runtime._identity import AgentId


UTC = timezone.utc


def _envelope() -> Envelope:
    return Envelope(
        sender=AgentId("agent", "sender"),
        target=AgentId("agent", "target"),
        content=[TextBlock(text="hello")],
        correlation_id="corr-1",
        causation_id="cause-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        actor_id="actor-1",
        event_id="env-1",
        event_type="agent.message",
    )


def test_envelope_span_from_envelope_carries_runtime_ids() -> None:
    span = EnvelopeSpan.from_envelope(
        _envelope(),
        name="dispatch",
        parent_span_id="parent-1",
        attributes=(("queue", "primary"),),
    )

    assert span.envelope_id == "env-1"
    assert span.correlation_id == "corr-1"
    assert span.trace_id == "trace-1"
    assert span.causation_id == "cause-1"
    assert span.parent_span_id == "parent-1"
    assert span.tenant_id == "tenant-1"
    assert span.workspace_id == "workspace-1"
    assert span.actor_id == "actor-1"
    assert span.sender == "agent/sender"
    assert span.target == "agent/target"
    assert span.attributes == (("queue", "primary"),)


def test_span_finish_records_terminal_state_and_duration() -> None:
    started_at = datetime.now(UTC)
    span = EnvelopeSpan(
        envelope_id="env-1",
        correlation_id="corr-1",
        started_at=started_at,
    )

    finished = span.finish(
        status=SpanStatus.FAILED,
        ended_at=started_at + timedelta(milliseconds=25),
        attributes=(("error", "timeout"),),
    )

    assert finished.status is SpanStatus.FAILED
    assert finished.is_finished
    assert finished.duration_ms == pytest.approx(25.0)
    assert finished.attributes == (("error", "timeout"),)


def test_span_finish_rejects_started_status() -> None:
    span = EnvelopeSpan(envelope_id="env-1", correlation_id="corr-1")

    with pytest.raises(ValueError, match="STARTED"):
        span.finish(status=SpanStatus.STARTED)


def test_span_query_validates_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        SpanQuery(limit=0)


def test_replay_request_and_deny_rule_validation_and_matching() -> None:
    request = ReplayRequest(
        envelope_id="env-1",
        correlation_id="corr-1",
        requested_by="ops",
        reason="debug",
        idempotency_key="idem-1",
    )

    assert ReplayDenyRule(
        envelope_id="env-1",
        reason="no replay",
        created_by="ops",
    ).matches(request)
    assert ReplayDenyRule(
        correlation_id="corr-1",
        reason="no replay",
        created_by="ops",
    ).matches(request)

    with pytest.raises(ValueError, match="envelope or correlation"):
        ReplayDenyRule(reason="empty", created_by="ops")


def test_kill_switch_target_from_envelope_and_rule_validation() -> None:
    target = KillSwitchTarget.from_envelope(_envelope())

    assert target.envelope_id == "env-1"
    assert target.correlation_id == "corr-1"
    assert target.tenant_id == "tenant-1"
    assert target.workspace_id == "workspace-1"
    assert target.actor_id == "actor-1"
    assert target.event_type == "agent.message"

    with pytest.raises(ValueError, match="value"):
        KillSwitchRule(
            scope=KillSwitchScope.TENANT,
            value="",
            reason="incident",
            activated_by="ops",
        )
