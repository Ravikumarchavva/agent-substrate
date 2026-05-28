"""Behavior tests for in-memory observability implementations."""

from __future__ import annotations

import asyncio

from ravi.extensions.observability import (
    InMemoryEnvelopeSpanRecorder,
    InMemoryOperatorKillSwitch,
    InMemoryReplayGate,
)
from ravi.kernel.observability import (
    EnvelopeSpan,
    EnvelopeSpanRecorder,
    KillSwitchRule,
    KillSwitchScope,
    KillSwitchTarget,
    OperatorKillSwitch,
    ReplayAdmissionStatus,
    ReplayDenyRule,
    ReplayGate,
    ReplayRequest,
    SpanQuery,
    SpanStatus,
)


class TestProtocolConformance:
    async def test_in_memory_types_satisfy_kernel_protocols(self) -> None:
        assert isinstance(InMemoryEnvelopeSpanRecorder(), EnvelopeSpanRecorder)
        assert isinstance(InMemoryReplayGate(), ReplayGate)
        assert isinstance(InMemoryOperatorKillSwitch(), OperatorKillSwitch)


class TestSpanRecording:
    async def test_span_lifecycle_start_finish_and_lookup(self) -> None:
        recorder = InMemoryEnvelopeSpanRecorder()
        span = EnvelopeSpan(
            envelope_id="env-1",
            correlation_id="corr-1",
            trace_id="trace-1",
        )

        recorded = await recorder.start_span(span)
        finished = await recorder.finish_span(
            recorded.span_id,
            status=SpanStatus.OK,
            attributes=(("handler", "agent"),),
        )

        assert finished.is_finished
        assert finished.status is SpanStatus.OK
        assert finished.duration_ms is not None
        assert finished.attributes == (("handler", "agent"),)
        assert await recorder.span_for(span.span_id) == finished

    async def test_envelope_and_correlation_lookup(self) -> None:
        recorder = InMemoryEnvelopeSpanRecorder()
        span_a = EnvelopeSpan(envelope_id="env-a", correlation_id="corr-1")
        span_b = EnvelopeSpan(envelope_id="env-b", correlation_id="corr-1")
        span_c = EnvelopeSpan(envelope_id="env-c", correlation_id="corr-2")

        await recorder.start_span(span_a)
        await recorder.start_span(span_b)
        await recorder.start_span(span_c)
        await recorder.finish_span(span_b.span_id, status=SpanStatus.FAILED)

        assert await recorder.spans_for_envelope("env-a") == (span_a,)
        correlated = await recorder.spans_for_correlation("corr-1")
        assert [span.envelope_id for span in correlated] == ["env-a", "env-b"]

        failed = await recorder.query_spans(
            SpanQuery(correlation_id="corr-1", status=SpanStatus.FAILED)
        )
        assert [span.envelope_id for span in failed] == ["env-b"]


class TestReplayGate:
    async def test_replay_allow_and_idempotent_duplicate(self) -> None:
        gate = InMemoryReplayGate()
        request = ReplayRequest(
            envelope_id="env-1",
            correlation_id="corr-1",
            requested_by="ops",
            reason="debug",
            idempotency_key="idem-1",
        )

        first = await gate.admit(request)
        duplicate = await gate.admit(request)
        stored = await gate.admission_for("idem-1")

        assert first.allowed
        assert first.status is ReplayAdmissionStatus.ALLOWED
        assert first.replay_token is not None
        assert duplicate.allowed
        assert duplicate.status is ReplayAdmissionStatus.DUPLICATE
        assert duplicate.replay_token == first.replay_token
        assert stored == first
        assert gate.admission_count() == 1

    async def test_replay_deny_then_clear_allows_new_admission(self) -> None:
        gate = InMemoryReplayGate()
        rule = ReplayDenyRule(
            envelope_id="env-1",
            reason="operator_hold",
            created_by="ops",
        )
        await gate.deny(rule)

        denied = await gate.admit(
            ReplayRequest(
                envelope_id="env-1",
                correlation_id="corr-1",
                requested_by="ops",
                reason="debug",
                idempotency_key="deny-1",
            )
        )
        cleared = await gate.clear_denial(rule.rule_id)
        allowed = await gate.admit(
            ReplayRequest(
                envelope_id="env-1",
                correlation_id="corr-1",
                requested_by="ops",
                reason="debug",
                idempotency_key="allow-1",
            )
        )

        assert not denied.allowed
        assert denied.status is ReplayAdmissionStatus.DENIED
        assert denied.reason == "operator_hold"
        assert cleared
        assert allowed.allowed


class TestKillSwitch:
    async def test_kill_switch_activation_matching_and_deactivation(self) -> None:
        switch = InMemoryOperatorKillSwitch()
        target = KillSwitchTarget(
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            correlation_id="corr-1",
            event_type="agent.created",
        )
        rule = KillSwitchRule(
            scope=KillSwitchScope.TENANT,
            value="tenant-1",
            reason="incident",
            activated_by="ops",
        )

        assert not (await switch.check(target)).blocked
        activated = await switch.activate(rule)
        blocked = await switch.check(target)
        deactivated = await switch.deactivate(activated.switch_id)

        assert blocked.blocked
        assert blocked.reason == "incident"
        assert blocked.scope is KillSwitchScope.TENANT
        assert blocked.matched_value == "tenant-1"
        assert deactivated
        assert not (await switch.check(target)).blocked

    async def test_kill_switch_event_type_glob(self) -> None:
        switch = InMemoryOperatorKillSwitch()
        await switch.activate(
            KillSwitchRule(
                scope=KillSwitchScope.EVENT_TYPE,
                value="agent.*",
                reason="pause_agent_events",
                activated_by="ops",
            )
        )

        assert (
            await switch.check(KillSwitchTarget(event_type="agent.completed"))
        ).blocked
        assert not (
            await switch.check(KillSwitchTarget(event_type="tool.completed"))
        ).blocked


class TestConcurrency:
    async def test_concurrent_recording_and_admission_from_threads(self) -> None:
        recorder = InMemoryEnvelopeSpanRecorder()
        gate = InMemoryReplayGate()

        def record_one(index: int) -> None:
            asyncio.run(
                recorder.start_span(
                    EnvelopeSpan(
                        envelope_id=f"env-{index}",
                        correlation_id="corr-shared",
                    )
                )
            )

        await asyncio.gather(
            *(asyncio.to_thread(record_one, index) for index in range(50))
        )
        assert recorder.count() == 50

        request = ReplayRequest(
            envelope_id="env-shared",
            correlation_id="corr-shared",
            requested_by="ops",
            reason="debug",
            idempotency_key="same-key",
        )

        def admit_once():
            return asyncio.run(gate.admit(request))

        admissions = await asyncio.gather(
            *(asyncio.to_thread(admit_once) for _ in range(25))
        )

        assert gate.admission_count() == 1
        assert sum(
            item.status is ReplayAdmissionStatus.ALLOWED for item in admissions
        ) == 1
        assert sum(
            item.status is ReplayAdmissionStatus.DUPLICATE for item in admissions
        ) == 24
        assert len({item.replay_token for item in admissions}) == 1
