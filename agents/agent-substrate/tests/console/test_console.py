"""Console package unit tests.

Coverage:
  stream_adapter  — event-log entries → typed UI events (including _RunFailed regression guard)
  subagents       — SubagentTracker ingestion, ordering, tree rendering
  status          — StatusLine.render() labels and styles
  live            — LiveTurn.failed flag via consume()
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from rich.console import Console as RichConsole
from rich.tree import Tree

from substrate.console.stream_adapter import (
    _RunFailed,
    stream_events,
)
from substrate.console.subagents import SubagentTracker
from substrate.console.status import StatusLine
from substrate.console.theme import DEFAULT_THEME
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)
from substrate.kernel.runtime.log_entry import RunLogEntry


# ---------------------------------------------------------------------------
# Fake runtime infrastructure
# ---------------------------------------------------------------------------


def _entry(kind: str, payload: dict[str, Any], seq: int = 0) -> RunLogEntry:
    return RunLogEntry(run_id="test-run", seq=seq, kind=kind, payload=payload)


@dataclass
class _FakeEventLog:
    """Replays a fixed list of RunLogEntry objects, then stops."""

    entries: list[RunLogEntry]

    async def tail(self, run_id: str, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        for e in self.entries:
            yield e


@dataclass
class _FakeRuntime:
    entries: list[RunLogEntry]
    event_log: _FakeEventLog = field(init=False)

    def __post_init__(self) -> None:
        self.event_log = _FakeEventLog(self.entries)

    async def register(self, agent: Any) -> None:
        pass

    async def submit(self, agent_id: Any, msg: Any) -> str:
        return "test-run"


@dataclass
class _FakeAgent:
    key: str = "agent"

    @property
    def id(self) -> AgentId:
        return AgentId(type="agent", key=self.key)


async def _collect(entries: list[RunLogEntry]) -> list[Any]:
    """Run stream_events over the given log entries; return all emitted events."""
    rt = _FakeRuntime(entries)
    agent = _FakeAgent()
    events: list[Any] = []
    async for ev in stream_events(rt, agent, "hello", correlation_id="cid-1"):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# stream_adapter — _RunFailed regression guard
# ---------------------------------------------------------------------------


async def test_run_failed_emits_run_failed_event() -> None:
    """run.failed log entry MUST produce _RunFailed — regression guard for the bug
    where the emit was silently dropped and the error appeared as 'completed · 0 tools'."""
    entries = [
        _entry("run.failed", {"error": "401 Unauthorized", "status": "agent_crashed"}, seq=0),
    ]
    events = await _collect(entries)

    failed_events = [e for e in events if isinstance(e, _RunFailed)]
    assert len(failed_events) == 1, "Expected exactly one _RunFailed event"
    assert failed_events[0].message == "401 Unauthorized"
    assert failed_events[0].status == "agent_crashed"


async def test_run_failed_is_followed_by_stream_done_error() -> None:
    """StreamDone(reason='error') must follow _RunFailed so the renderer stops."""
    entries = [_entry("run.failed", {"error": "boom"}, seq=0)]
    events = await _collect(entries)

    types = [type(e).__name__ for e in events]
    assert "_RunFailed" in types
    assert "StreamDone" in types
    # _RunFailed must come before StreamDone
    assert types.index("_RunFailed") < types.index("StreamDone")
    done = next(e for e in events if isinstance(e, StreamDone))
    assert done.reason == "error"


async def test_run_cancelled_emits_run_failed_with_cancelled_status() -> None:
    entries = [_entry("run.cancelled", {}, seq=0)]
    events = await _collect(entries)

    failed_events = [e for e in events if isinstance(e, _RunFailed)]
    assert len(failed_events) == 1
    assert failed_events[0].status == "cancelled"
    done = next(e for e in events if isinstance(e, StreamDone))
    assert done.reason == "cancelled"


# ---------------------------------------------------------------------------
# stream_adapter — happy path
# ---------------------------------------------------------------------------


async def test_text_delta_emitted() -> None:
    entries = [
        _entry("text.delta", {"text": "Hello"}, seq=0),
        _entry("text.delta", {"text": " world"}, seq=1),
        _entry("run.completed", {}, seq=2),
    ]
    events = await _collect(entries)

    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(deltas) == 2
    assert deltas[0].text == "Hello"
    assert deltas[1].text == " world"


async def test_reasoning_delta_emitted() -> None:
    entries = [
        _entry("reasoning.delta", {"text": "thinking…"}, seq=0),
        _entry("run.completed", {}, seq=1),
    ]
    events = await _collect(entries)

    assert any(isinstance(e, ReasoningDelta) and e.text == "thinking…" for e in events)


async def test_tool_call_and_result_produce_agent_progress() -> None:
    entries = [
        _entry("tool.call", {"tool_name": "calculator"}, seq=0),
        _entry("tool.result", {"tool_name": "calculator", "ok": True}, seq=1),
        _entry("run.completed", {}, seq=2),
    ]
    events = await _collect(entries)

    progress = [e for e in events if isinstance(e, AgentProgress)]
    assert len(progress) == 2
    call_ev, result_ev = progress
    assert call_ev.step == AgentStep.TOOL_CALL
    assert call_ev.content == "calculator"
    assert call_ev.depth == 0  # main-agent tool
    assert result_ev.step == AgentStep.TOOL_RESULT


async def test_failed_tool_result_content_tagged() -> None:
    entries = [
        _entry("tool.call", {"tool_name": "broken"}, seq=0),
        _entry("tool.result", {"tool_name": "broken", "ok": False}, seq=1),
        _entry("run.completed", {}, seq=2),
    ]
    events = await _collect(entries)

    result_ev = next(
        e for e in events
        if isinstance(e, AgentProgress) and e.step == AgentStep.TOOL_RESULT
    )
    assert "error" in result_ev.content


async def test_run_completed_emits_completion_then_done() -> None:
    entries = [
        _entry("text.delta", {"text": "Hi"}, seq=0),
        _entry("run.completed", {}, seq=1),
    ]
    events = await _collect(entries)

    types = [type(e).__name__ for e in events]
    assert "CompletionEvent" in types
    assert "StreamDone" in types
    done = next(e for e in events if isinstance(e, StreamDone))
    assert done.reason == "success"


async def test_subagent_start_and_done_produce_depth1_progress() -> None:
    entries = [
        _entry("subagent.start", {"agent": "worker", "parent": "orchestrator", "task": "do it"}, seq=0),
        _entry("subagent.done", {"agent": "worker", "parent": "orchestrator", "ok": True}, seq=1),
        _entry("run.completed", {}, seq=2),
    ]
    events = await _collect(entries)

    subagent_events = [
        e for e in events
        if isinstance(e, AgentProgress) and e.depth == 1
    ]
    assert len(subagent_events) == 2
    assert subagent_events[0].agent_id.key == "worker"
    assert subagent_events[0].step == AgentStep.THINKING
    assert subagent_events[1].step == AgentStep.DONE


async def test_failed_subagent_done_maps_to_error_step() -> None:
    entries = [
        _entry("subagent.start", {"agent": "flaky", "parent": "orch"}, seq=0),
        _entry("subagent.done", {"agent": "flaky", "parent": "orch", "ok": False}, seq=1),
        _entry("run.completed", {}, seq=2),
    ]
    events = await _collect(entries)

    done_ev = next(
        e for e in events
        if isinstance(e, AgentProgress) and e.agent_id.key == "flaky" and e.step != AgentStep.THINKING
    )
    assert done_ev.step == AgentStep.ERROR


# ---------------------------------------------------------------------------
# SubagentTracker
# ---------------------------------------------------------------------------


def _ap(key: str, step: AgentStep, *, parent: str | None = None, depth: int = 1, seq: int = 0) -> AgentProgress:
    return AgentProgress(
        agent_id=AgentId(type="agent", key=key),
        step=step,
        content="",
        parent_id=AgentId(type="agent", key=parent) if parent else None,
        depth=depth,
        seq=seq,
    )


def test_subagent_tracker_empty_has_no_subagents() -> None:
    tracker = SubagentTracker()
    assert not tracker.has_subagents
    assert tracker.render(DEFAULT_THEME) is None


def test_subagent_tracker_depth0_only_has_no_subagents() -> None:
    tracker = SubagentTracker()
    tracker.ingest(_ap("main", AgentStep.THINKING, depth=0))
    assert not tracker.has_subagents


def test_subagent_tracker_depth1_sets_has_subagents() -> None:
    tracker = SubagentTracker()
    tracker.ingest(_ap("worker", AgentStep.THINKING, parent="orch", depth=1))
    assert tracker.has_subagents


def test_subagent_tracker_renders_tree_with_subagent() -> None:
    tracker = SubagentTracker()
    tracker.ingest(_ap("worker", AgentStep.DONE, parent="orch", depth=1, seq=1))
    tree = tracker.render(DEFAULT_THEME)
    assert isinstance(tree, Tree)


def test_subagent_tracker_seq_ordering_later_overwrites_earlier() -> None:
    """A later seq should advance the node's step; an earlier seq must not regress it."""
    tracker = SubagentTracker()
    tracker.ingest(_ap("w", AgentStep.THINKING, parent="o", depth=1, seq=5))
    tracker.ingest(_ap("w", AgentStep.DONE, parent="o", depth=1, seq=10))
    assert tracker.nodes["w"].step == AgentStep.DONE

    # Out-of-order: older seq should not overwrite
    tracker.ingest(_ap("w", AgentStep.ERROR, parent="o", depth=1, seq=3))
    assert tracker.nodes["w"].step == AgentStep.DONE  # unchanged


def test_subagent_tracker_auto_creates_missing_parent() -> None:
    """Child references a parent key that hasn't been ingested yet — parent must be created."""
    tracker = SubagentTracker()
    tracker.ingest(_ap("child", AgentStep.THINKING, parent="missing-parent", depth=1))
    assert "missing-parent" in tracker.nodes


def test_subagent_tracker_multiple_children_under_same_parent() -> None:
    tracker = SubagentTracker()
    tracker.ingest(_ap("a", AgentStep.THINKING, parent="root", depth=1, seq=0))
    tracker.ingest(_ap("b", AgentStep.DONE, parent="root", depth=1, seq=1))
    assert tracker.has_subagents
    # Both children tracked
    assert "a" in tracker.nodes
    assert "b" in tracker.nodes


# ---------------------------------------------------------------------------
# StatusLine
# ---------------------------------------------------------------------------


def test_status_line_render_running() -> None:
    sl = StatusLine(model="gpt-test")
    text = sl.render(DEFAULT_THEME)
    plain = text.plain
    assert "running" in plain
    assert "gpt-test" in plain
    assert text.style != "error"


def test_status_line_render_done() -> None:
    sl = StatusLine(model="gpt-test")
    text = sl.render(DEFAULT_THEME, done=True)
    assert "completed" in text.plain
    assert "failed" not in text.plain


def test_status_line_render_failed() -> None:
    sl = StatusLine(model="gpt-test")
    text = sl.render(DEFAULT_THEME, failed=True)
    assert "failed" in text.plain
    assert str(text.style) == "error"


def test_status_line_failed_overrides_done() -> None:
    """failed=True takes precedence over done=True."""
    sl = StatusLine(model="x")
    text = sl.render(DEFAULT_THEME, done=True, failed=True)
    assert "failed" in text.plain


def test_status_line_tool_count_singular() -> None:
    sl = StatusLine(model="x", tool_calls=1)
    assert "1 tool" in sl.render(DEFAULT_THEME).plain


def test_status_line_tool_count_plural() -> None:
    sl = StatusLine(model="x", tool_calls=3)
    assert "3 tools" in sl.render(DEFAULT_THEME).plain


# ---------------------------------------------------------------------------
# LiveTurn — failed flag
# ---------------------------------------------------------------------------


def _make_turn() -> Any:
    """Build a LiveTurn backed by a non-terminal StringIO console (sequential mode)."""
    from substrate.console.live import LiveTurn

    buf = RichConsole(file=io.StringIO(), highlight=False)
    sl = StatusLine(model="test")
    return LiveTurn(buf, name="agent", theme=DEFAULT_THEME, status=sl)


async def _feed(turn: Any, events: list[Any]) -> str:
    async def _gen() -> AsyncIterator[Any]:
        for e in events:
            yield e

    return await turn.consume(_gen())


async def test_live_turn_sets_failed_flag_on_run_failed() -> None:
    """LiveTurn.failed must be True after consuming a _RunFailed event."""
    from substrate.console.live import LiveTurn

    turn = _make_turn()
    assert isinstance(turn, LiveTurn)
    assert not turn.failed

    await _feed(turn, [
        _RunFailed(message="something went wrong", status="agent_crashed"),
        StreamDone(reason="error"),
    ])

    assert turn.failed, "LiveTurn.failed must be True after _RunFailed"


async def test_live_turn_failed_false_on_success() -> None:
    """LiveTurn.failed stays False on a normal completion."""
    from substrate.kernel.core.content import TextBlock

    turn = _make_turn()
    await _feed(turn, [
        TextDelta(text="Hello"),
        CompletionEvent(content=[TextBlock(text="Hello")]),
        StreamDone(reason="success"),
    ])

    assert not turn.failed


async def test_live_turn_returns_assistant_text() -> None:
    from substrate.kernel.core.content import TextBlock

    turn = _make_turn()
    result = await _feed(turn, [
        TextDelta(text="Hello "),
        TextDelta(text="world"),
        CompletionEvent(content=[TextBlock(text="Hello world")]),
        StreamDone(reason="success"),
    ])

    assert result == "Hello world"
