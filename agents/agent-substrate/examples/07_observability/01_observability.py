"""Example 07-1: Observability — OpenTelemetry tracing, EnvelopeSpan, ReplayGate, OperatorKillSwitch.

Demonstrates the ravi-engine observability layer:
- configure_opentelemetry() wires up OTel SDK (console or OTLP to Tempo/Jaeger)
- AssistantAgent emits spans automatically on every run
- EnvelopeSpan wraps arbitrary operations with structured span metadata
- InMemoryReplayGate gates idempotent replay admissions
- InMemoryOperatorKillSwitch enables/disables traffic by scope

In production, set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT to ship spans to Grafana Tempo or Jaeger.
"""

import asyncio
import os

from ravi.config import settings
from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.adapters.observability import (
    InMemoryEnvelopeSpanRecorder,
    InMemoryOperatorKillSwitch,
    InMemoryReplayGate,
)
from ravi.fabric.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from ravi.adapters.llm.factory import create_model_client
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.fabric.memory.unbounded import UnboundedMemory
from ravi.kernel.observability import (
    EnvelopeSpan,
    KillSwitchRule,
    KillSwitchScope,
    KillSwitchTarget,
    ReplayDenyRule,
    ReplayRequest,
    SpanStatus,
)
from ravi.serving.shared.observability import configure_opentelemetry

# Infrastructure: none required — all sections use in-memory implementations.
#   For OTLP export to Grafana Tempo or Jaeger, set:
#     OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318
#   Then start the stack: cd ravi-engine && make infra-up

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")

# ---


async def section_1_configure_otel() -> None:
    """Section 1 — Configure OpenTelemetry.

    configure_opentelemetry() is idempotent — safe to call multiple times.
    Supported backends:
      - Console exporter  (export_traces_to_console=True)
      - OTLP/HTTP         (otlp_trace_endpoint=<url>)  — Grafana Tempo, Jaeger, Honeycomb
      - OTLP/gRPC         (otlp_metric_endpoint=<url>) — set via env var
    """
    print("=== Section 1: Configure OpenTelemetry ===")

    if OTLP_ENDPOINT:
        configure_opentelemetry(
            service_name="my-agent",
            otlp_trace_endpoint=OTLP_ENDPOINT,
        )
        print(f"OTel configured — shipping spans to {OTLP_ENDPOINT}")
    else:
        configure_opentelemetry(
            service_name="my-agent",
            export_traces_to_console=True,
        )
        print("OTel configured — exporting spans to console")
        print("Tip: set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT to ship to Tempo/Jaeger")


# ---


async def section_2_agent_with_tracing() -> None:
    """Section 2 — Run a AssistantAgent; spans are emitted automatically."""
    print("\n=== Section 2: AssistantAgent with automatic tracing ===")

    api_keys = {
        "openai": settings.OPENAI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "google": settings.GEMINI_API_KEY,
        "groq": settings.GROQ_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }

    if not any(api_keys.values()):
        print("No API key configured — skipping live agent run.")
        print("Set OPENAI_API_KEY (or another provider key) to run this section.")
        return

    catalog = AgentCatalog()
    catalog.register_model(
        "primary",
        create_model_client(settings.CHAT_MODEL, api_keys=api_keys),
    )
    catalog.register_memory("memory", UnboundedMemory())
    for tool in [CalculatorTool(), GetCurrentTimeTool()]:
        catalog.register_tool(tool)

    agent = AssistantAgent(
        name="ObservabilityBot",
        description="Demo agent for observability example.",
        catalog=catalog,
        max_iterations=3,
        verbose=False,
    )

    result = await agent.run("What is 12 multiplied by 8?")
    print(f"Agent answer: {result.output_text}")
    print("Spans were emitted automatically to the configured OTel backend.")


# ---


async def section_3_envelope_span() -> None:
    """Section 3 — EnvelopeSpan: wrap an operation with structured span metadata."""
    print("\n=== Section 3: EnvelopeSpan ===")

    recorder = InMemoryEnvelopeSpanRecorder()

    span = EnvelopeSpan(
        envelope_id="env-20240115-001",
        correlation_id="corr-req-42",
        name="invoice.extract",
        tenant_id="acme-corp",
        workspace_id="ws-finance",
        actor_id="agent-extractor",
        event_type="InvoiceExtractionRequested",
        attributes=(
            ("invoice.vendor", "Acme Corp"),
            ("invoice.total_usd", "50.00"),
        ),
    )

    started = await recorder.start_span(span)
    print(f"Span started  : {started.span_id[:12]}... name={started.name!r}")
    print(f"  envelope_id : {started.envelope_id}")
    print(f"  status      : {started.status.name}")
    print(f"  attributes  : {dict(started.attributes)}")

    await asyncio.sleep(0.01)

    finished = await recorder.finish_span(
        started.span_id,
        status=SpanStatus.OK,
        attributes=(("invoice.pages_extracted", "3"),),
    )
    print(
        f"Span finished : status={finished.status.name}  duration={finished.duration_ms:.1f}ms"
    )

    spans = await recorder.spans_for_correlation("corr-req-42")
    print(f"Spans for correlation 'corr-req-42': {len(spans)}")
    print(f"Total spans in recorder: {recorder.count()}")


# ---


async def section_4_replay_gate() -> None:
    """Section 4 — ReplayGate: idempotent replay admission."""
    print("\n=== Section 4: ReplayGate ===")

    gate = InMemoryReplayGate()

    request = ReplayRequest(
        envelope_id="env-20240115-001",
        correlation_id="corr-req-42",
        requested_by="ops-engineer",
        reason="Client reported missing data — replaying for audit",
    )

    # First admission — ALLOWED
    decision = await gate.admit(request)
    print(
        f"First admission : allowed={decision.allowed}  status={decision.status.name}"
    )
    print(f"  replay_token  : {decision.replay_token}")

    # Same idempotency key — returns the original decision (DUPLICATE)
    duplicate = await gate.admit(request)
    print(
        f"Duplicate admit : allowed={duplicate.allowed}  status={duplicate.status.name}"
    )

    looked_up = await gate.admission_for(request.idempotency_key)
    assert looked_up is not None
    print(
        f"Looked up       : {looked_up.idempotency_key[:12]}...  token={looked_up.replay_token}"
    )

    # Add a deny rule — subsequent replay for that envelope is DENIED
    deny_rule = ReplayDenyRule(
        envelope_id="env-20240115-002",
        reason="Contains PII — replay blocked by compliance",
        created_by="compliance-bot",
    )
    await gate.deny(deny_rule)

    denied_request = ReplayRequest(
        envelope_id="env-20240115-002",
        correlation_id="corr-pii-99",
        requested_by="analyst",
        reason="Debugging",
    )
    denied = await gate.admit(denied_request)
    print(f"Denied request  : allowed={denied.allowed}  reason={denied.reason!r}")
    print(f"Active deny rules: {len(await gate.deny_rules())}")


# ---


async def section_5_kill_switch() -> None:
    """Section 5 — OperatorKillSwitch: block traffic by scope."""
    print("\n=== Section 5: OperatorKillSwitch ===")

    ks = InMemoryOperatorKillSwitch()

    rule = KillSwitchRule(
        scope=KillSwitchScope.TENANT,
        value="tenant-suspended",
        reason="Account suspended for non-payment",
        activated_by="billing-system",
    )
    activated = await ks.activate(rule)
    print(
        f"Kill switch activated : id={activated.switch_id[:12]}...  scope={activated.scope.name}"
    )
    print(f"Active switches: {ks.count()}")

    # Matching target — blocked
    blocked_target = KillSwitchTarget(tenant_id="tenant-suspended")
    decision = await ks.check(blocked_target)
    print(f"Blocked target  : blocked={decision.blocked}  reason={decision.reason!r}")

    # Non-matching target — passes
    ok_decision = await ks.check(KillSwitchTarget(tenant_id="tenant-good"))
    print(f"Allowed target  : blocked={ok_decision.blocked}")

    # Deactivate (e.g. after payment received)
    removed = await ks.deactivate(activated.switch_id)
    print(f"Switch deactivated: {removed}  active now={ks.count()}")

    reopened = await ks.check(blocked_target)
    print(f"After deactivation: blocked={reopened.blocked}")


# ---


async def main() -> None:
    await section_1_configure_otel()
    await section_2_agent_with_tracing()
    await section_3_envelope_span()
    await section_4_replay_gate()
    await section_5_kill_switch()

    print("\n--- Production note ---")
    print(
        "Set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://tempo:4318 to ship spans to Grafana Tempo."
    )
    print("Start the full stack: cd ravi-engine && make infra-up")


if __name__ == "__main__":
    asyncio.run(main())
