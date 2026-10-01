"""Invariant register — illegal states unrepresentable (row I29).

Every row here is a defect reproduced during the audit, written as the
behaviour the rewrite must have. They are cheap, they run in milliseconds, and
together they are the permanent lock on a whole class of defect: a type that
accepts a state its consumers cannot handle.

Forward-looking imports live inside test bodies on purpose — a module that
does not exist yet turns into an xfail for that one row instead of a
collection error that hides the rest of the file.
"""

from __future__ import annotations

import copy
import pickle
from datetime import datetime, timezone

import pytest

from substrate.kernel.abstractions.core.content import (
    ChatMessage,
    DataBlock,
    Role,
    TextBlock,
    ToolUseBlock,
    parse_content_block,
)
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.core.usage import Usage

# --------------------------------------------------------------------------
# Content model
# --------------------------------------------------------------------------


def test_a_block_without_a_type_is_rejected() -> None:
    """``UnknownBlock`` exists for *future provider* types, not for malformed
    input. Swallowing a missing discriminator turns a bug into lost content."""
    from substrate.kernel.abstractions.exceptions import BlockValidationError

    with pytest.raises(BlockValidationError):
        parse_content_block({"text": "hello"})


def test_a_misspelled_known_type_is_rejected() -> None:
    from substrate.kernel.abstractions.exceptions import BlockValidationError

    with pytest.raises(BlockValidationError):
        parse_content_block({"type": "txt", "text": "hello"})


def test_content_is_deeply_immutable_and_hashable() -> None:
    """Messages are passed across agents, cached and journaled. A mutable
    'immutable' message means one consumer can corrupt another's copy."""
    message = ChatMessage(role=Role.USER, content="hi")
    with pytest.raises((AttributeError, TypeError)):
        message.content.append(TextBlock(text="injected"))  # type: ignore[attr-defined]
    hash(message)
    hash(ToolUseBlock(call_id="1", tool_name="x", arguments={"a": 1}))


def test_structured_blocks_render_non_json_values() -> None:
    """``str(block)`` is used to build prompts. It must never raise."""
    assert str(DataBlock(data={"b": b"\x00"}))
    assert str(DataBlock(data={"d": datetime.now(tz=timezone.utc)}))


# --------------------------------------------------------------------------
# Execution metadata
# --------------------------------------------------------------------------


def test_a_naive_deadline_is_rejected_at_construction() -> None:
    """``check()`` runs at every cooperative yield point. A deadline that makes
    it raise ``TypeError`` disables cancellation everywhere at once."""
    from substrate.kernel.abstractions.agent.runtime_context import RunMeta

    class _Token:
        is_cancelled = False

        def cancel(self, reason: str = "") -> None: ...
        def check(self) -> None: ...
        def wait(self) -> None: ...
        def add_callback(self, callback: object) -> None: ...
        def child(self) -> "_Token":
            return self

    with pytest.raises((ValueError, TypeError)):
        RunMeta(run_id="r", cancellation=_Token(), deadline=datetime.now())  # naive


def test_structured_exceptions_survive_a_process_boundary() -> None:
    """Workers may run in another process. An exception that cannot be pickled
    is reported as a pickling error instead of the real failure."""
    from substrate.kernel.abstractions.exceptions import (
        AgentCrashError,
        BranchHeadConflictError,
        ConcurrentAppendError,
        SnapshotConflictError,
        ThreadBusyError,
    )

    errors: list[Exception] = [
        ConcurrentAppendError("x", run_id="r", expected_seq=1, actual_seq=2),
        ThreadBusyError("x", thread_id="t"),
        BranchHeadConflictError("x", session_id="s", branch_id="b", expected=1, actual=2),
        AgentCrashError("x", run_id="r", agent_id=Actor("a")),
        SnapshotConflictError(
            "x", session_id="s", branch_id="b", expected_parent_id=None, actual_parent_id="p"
        ),
    ]
    for error in errors:
        pickle.loads(pickle.dumps(error))
        copy.copy(error)


def test_usage_accumulates_with_sum() -> None:
    """Totalling usage across turns is the single most common thing a caller
    does with it."""
    total = sum([Usage(input_tokens=1), Usage(input_tokens=2)], Usage())
    assert total.input_tokens == 3
    assert sum([Usage(input_tokens=1), Usage(input_tokens=2)]).input_tokens == 3  # type: ignore[arg-type]


def test_tool_risk_is_ordered_by_severity() -> None:
    """Risk is compared to decide whether approval is required. String ordering
    silently inverts that decision."""
    from substrate.kernel.abstractions.tools import ToolRisk

    assert ToolRisk.SAFE < ToolRisk.HIGH < ToolRisk.CRITICAL
    assert not ToolRisk.SAFE > ToolRisk.CRITICAL


def test_supervision_survives_a_round_trip_with_an_unknown_field() -> None:
    """Supervision is persisted and read back by a possibly older or newer
    worker. An unknown field must not crash the read."""
    from substrate.kernel.abstractions.agent.supervision import Supervision

    data = Supervision.root(Actor("a", "k")).to_dict()
    data["execution_budget"]["max_tool_calls"] = 5  # a field this version doesn't know
    Supervision.from_dict(data)


# --------------------------------------------------------------------------
# Kind-plus-optional types: states no consumer can handle
# --------------------------------------------------------------------------


def test_tagged_results_cannot_contradict_their_tag() -> None:
    from substrate.kernel.abstractions.document import ExtractionResult
    from substrate.kernel.abstractions.runtime.communication import AskOutcome
    from substrate.kernel.abstractions.runtime.wakeup import Wakeup
    from substrate.kernel.abstractions.tools.approval import ApprovalDecision, ApprovalResult

    with pytest.raises(ValueError):
        Wakeup(kind="timer")  # a timer with no time
    with pytest.raises(ValueError):
        Wakeup(kind="signal")  # a signal wait with no signal names
    with pytest.raises(ValueError):
        AskOutcome(kind="replied")  # replied with no reply
    with pytest.raises(ValueError):
        ApprovalResult(decision=ApprovalDecision.MODIFIED)  # modified with no args
    with pytest.raises(ValueError):
        ExtractionResult(success=True, error="boom")  # succeeded, with an error


def test_workspace_paths_cannot_escape_the_workspace() -> None:
    """A manifest path comes from tool output and names a file to materialise."""
    from substrate.kernel.abstractions.storage.snapshots import ContentRef, WorkspaceFileEntry

    ref = ContentRef(hash="x", size_bytes=1)
    for hostile in ("../../etc/passwd", "/etc/passwd"):
        with pytest.raises(ValueError):
            WorkspaceFileEntry(path=hostile, content=ref)


def test_effect_identity_handles_any_json_encodable_argument() -> None:
    """Effect identity is computed from tool arguments, which routinely contain
    bytes and timestamps."""
    from substrate.kernel.abstractions.runtime.effects import Effect

    assert Effect.make_id("r", "0", "k", {"b": b"\x00"})
    assert Effect.make_id("r", "0", "k", {"d": datetime.now(tz=timezone.utc)})


def test_ids_are_time_sortable() -> None:
    """Time-sortable ids are what make a log or inbox orderable without a
    separate sequence column."""
    import time

    from substrate.kernel.abstractions.ids import new_id

    first = new_id()
    time.sleep(0.002)
    second = new_id()
    assert first < second
