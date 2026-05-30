"""Regression tests for the kernel hardening pass.

Each test pins one fix from the Step-0 hardening pass so it cannot silently
regress as further runtime / fabric work lands.

B1  — No upward imports from ravi.kernel into ravi.{logger,shared,...}
B2  — kernel.contracts._event_fabric.py is gone; the single canonical
      EventFabric contract lives in kernel.events._fabric.
B3  — Envelope ⇆ EventEnvelope[list[ContentBlock]] roundtrip is lossless,
      and shared.events.envelope.EventEnvelope subclasses the kernel one.
B9  — RunCheckpoint.to_ref / CheckpointRef.from_run_checkpoint produce a
      consistent slim pointer into the persisted tree.
B10 — LocalRuntime._normalize_content rejects duck-typed imposters and
      coerces them to TextBlock.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ravi.kernel.contracts._event import EventEnvelope as KernelEventEnvelope
from ravi.kernel.messages.content import CONTENT_BLOCK_TYPES, TextBlock
from ravi.agents.checkpoint import RunCheckpoint
from ravi.kernel.runtime._contracts import Envelope
from ravi.kernel.runtime._identity import (
    AgentId,
    IdentityContext,
    PrincipalId,
    PrincipalKind,
)
from ravi.kernel.runtime._lifecycle import CheckpointRef
from ravi.agents.runtime.local import LocalRuntime
from ravi.shared.events.envelope import EventEnvelope as SharedEventEnvelope


REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = REPO_ROOT / "src" / "ravi" / "kernel"


# ---------------------------------------------------------------------------
# B1 — kernel is independent (no upward imports)
# ---------------------------------------------------------------------------


class TestB1KernelIndependence:
    """``ravi.kernel`` must never import from ``ravi.logger`` (or any layer above)."""

    FORBIDDEN_PREFIXES = (
        "ravi.logger",
        "ravi.shared",
        "ravi.adapters",
        "ravi.catalog",
        "ravi.fabric",
        "ravi.reasoning",
        "ravi.orchestration",
        "ravi.guardrails",
        "ravi.platform",
        "ravi.server",
        "ravi.services",
        "ravi.configs",
    )

    def test_no_upward_imports_in_kernel_source(self) -> None:
        violations: list[str] = []
        for path in KERNEL_DIR.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            # Strip triple-quoted docstring/example blocks so docstring text
            # like ``from ravi.adapters.llm.factory import X`` does not
            # trigger a false positive.
            stripped = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
            stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
            for prefix in self.FORBIDDEN_PREFIXES:
                pattern = rf"^\s*(?:from\s+{re.escape(prefix)}|import\s+{re.escape(prefix)})"
                for m in re.finditer(pattern, stripped, re.MULTILINE):
                    rel = path.relative_to(REPO_ROOT)
                    violations.append(f"{rel}: {m.group(0).strip()}")
        assert not violations, (
            "kernel must not import from above; violations:\n  "
            + "\n  ".join(violations)
        )


# ---------------------------------------------------------------------------
# B2 — single canonical EventFabric contract
# ---------------------------------------------------------------------------


class TestB2SingleEventFabric:
    def test_dead_event_fabric_file_removed(self) -> None:
        dead = KERNEL_DIR / "contracts" / "_event_fabric.py"
        assert not dead.exists(), (
            f"{dead} must be gone; the canonical EventFabric lives in "
            f"ravi.kernel.events._fabric"
        )

    def test_event_fabric_imports_from_kernel(self) -> None:
        from ravi.kernel.contracts import (
            DurableEventLog,
            EventFabric,
            PublishRequest,
            RealtimeFanout,
        )
        from ravi.kernel.events._fabric import (
            EventFabric as CanonicalFabric,
            DurableEventLog as CanonicalLog,
            RealtimeFanout as CanonicalFanout,
            PublishRequest as CanonicalRequest,
        )

        assert EventFabric is CanonicalFabric
        assert DurableEventLog is CanonicalLog
        assert RealtimeFanout is CanonicalFanout
        assert PublishRequest is CanonicalRequest


# ---------------------------------------------------------------------------
# B3 — unified envelope wire format
# ---------------------------------------------------------------------------


def _make_identity(name: str = "alice") -> IdentityContext:
    return IdentityContext(
        principal=PrincipalId(
            kind=PrincipalKind.AGENT,
            tenant_id="t1",
            workspace_id="w1",
            name=name,
        )
    )


class TestB3EnvelopeUnification:
    def test_envelope_derives_tenancy_from_identity(self) -> None:
        ident = _make_identity()
        env = Envelope(
            sender=AgentId("a", "1"),
            target=AgentId("b", "2"),
            content=[TextBlock(text="x")],
            identity=ident,
        )
        assert env.tenant_id == "t1"
        assert env.workspace_id == "w1"
        assert env.actor_id == "agent/t1/w1/alice"

    def test_envelope_to_event_envelope_roundtrip(self) -> None:
        ident = _make_identity()
        env = Envelope(
            sender=AgentId("a", "1"),
            target=AgentId("b", "2"),
            content=[TextBlock(text="hello")],
            event_type="agent.message",
            identity=ident,
        )
        wire = env.to_event_envelope()

        # All fabric metadata survives the trip
        assert wire.event_type == "agent.message"
        assert wire.tenant_id == "t1"
        assert wire.workspace_id == "w1"
        assert wire.correlation_id == env.correlation_id
        assert wire.identity is ident
        assert isinstance(wire.payload, list)
        assert wire.payload[0].text == "hello"  # type: ignore[union-attr]

        back = wire.to_runtime_envelope()
        assert back.content[0].text == "hello"  # type: ignore[union-attr]
        assert back.identity is ident
        assert back.tenant_id == "t1"
        assert back.event_type == "agent.message"

    def test_envelope_requires_event_type_for_wire(self) -> None:
        env = Envelope(
            sender=None,
            target=AgentId("b", "2"),
            content=[TextBlock(text="x")],
        )
        with pytest.raises(ValueError, match="event_type"):
            env.to_event_envelope()

    def test_envelope_to_wire_with_explicit_event_type(self) -> None:
        env = Envelope(
            sender=None,
            target=AgentId("b", "2"),
            content=[TextBlock(text="x")],
        )
        wire = env.to_event_envelope(event_type="ad.hoc")
        assert wire.event_type == "ad.hoc"

    def test_shared_envelope_subclasses_kernel(self) -> None:
        assert issubclass(SharedEventEnvelope, KernelEventEnvelope)

    def test_shared_envelope_keeps_default_payload(self) -> None:
        e = SharedEventEnvelope(event_type="thread.created")
        assert e.payload == {}

    def test_shared_envelope_inherits_fabric_fields(self) -> None:
        e = SharedEventEnvelope(event_type="thread.created")
        # Inherited from kernel envelope
        assert hasattr(e, "identity")
        assert hasattr(e, "trust")
        assert hasattr(e, "activation")
        assert hasattr(e, "placement")
        assert hasattr(e, "provenance")

    def test_to_runtime_envelope_rejects_non_list_payload(self) -> None:
        wire = SharedEventEnvelope(event_type="thread.created", payload={"a": 1})
        with pytest.raises(TypeError, match="list"):
            wire.to_runtime_envelope()


# ---------------------------------------------------------------------------
# B9 — CheckpointRef ⇆ RunCheckpoint reconciliation
# ---------------------------------------------------------------------------


class TestB9CheckpointReconciliation:
    def test_run_checkpoint_to_ref(self) -> None:
        cp = RunCheckpoint(run_id="r1", agent_id="alice", iteration=7)
        ref = cp.to_ref(store_uri="redis://localhost:6379/0", byte_size=512)
        assert ref.run_id == "r1"
        assert ref.agent_id_str == "alice"
        assert ref.checkpoint_id == cp.checkpoint_id
        assert ref.sequence == 7
        assert ref.store_uri == "redis://localhost:6379/0"
        assert ref.byte_size == 512

    def test_checkpoint_ref_from_run_checkpoint_classmethod(self) -> None:
        cp = RunCheckpoint(run_id="r2", agent_id="bob", iteration=0)
        ref = CheckpointRef.from_run_checkpoint(cp, store_uri="s3://bucket/k")
        assert ref.run_id == "r2"
        assert ref.agent_id_str == "bob"
        assert ref.checkpoint_id == cp.checkpoint_id

    def test_checkpoint_ref_run_id_default_empty(self) -> None:
        # Constructing a CheckpointRef directly without run_id still works
        ref = CheckpointRef(
            agent_id_str="x",
            checkpoint_id="abc",
            sequence=1,
            store_uri="mem://",
        )
        assert ref.run_id == ""


# ---------------------------------------------------------------------------
# B10 — _normalize_content strict validation
# ---------------------------------------------------------------------------


class TestB10NormalizeContentStrict:
    def test_real_text_block_passes_through(self) -> None:
        out = LocalRuntime._normalize_content([TextBlock(text="hi")])
        assert isinstance(out[0], TextBlock)
        assert out[0].text == "hi"

    def test_duck_typed_imposter_is_coerced(self) -> None:
        class Imposter:
            type = "text"

            def model_dump(self) -> dict:
                return {"type": "text"}

        out = LocalRuntime._normalize_content([Imposter()])
        # Strict check: imposter is NOT a ContentBlock, must be coerced
        assert isinstance(out[0], TextBlock)
        assert not isinstance(out[0], Imposter)

    def test_string_in_list_becomes_text_block(self) -> None:
        out = LocalRuntime._normalize_content(["plain text"])
        assert isinstance(out[0], TextBlock)
        assert out[0].text == "plain text"

    def test_mixed_list_block_string_int(self) -> None:
        out = LocalRuntime._normalize_content([TextBlock(text="a"), "b", 42])
        assert all(isinstance(b, TextBlock) for b in out)
        assert [b.text for b in out] == ["a", "b", "42"]

    def test_content_block_types_includes_all_concrete_variants(self) -> None:
        from ravi.kernel.messages.content import (
            AudioBlock,
            CodeBlock,
            DataBlock,
            DocumentBlock,
            ErrorBlock,
            ImageBlock,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
            VideoBlock,
        )

        expected = {
            TextBlock,
            ImageBlock,
            AudioBlock,
            VideoBlock,
            DocumentBlock,
            DataBlock,
            CodeBlock,
            ErrorBlock,
            ToolUseBlock,
            ToolResultBlock,
            ThinkingBlock,
        }
        assert set(CONTENT_BLOCK_TYPES) == expected
