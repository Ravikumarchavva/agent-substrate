"""AG-UI (https://docs.ag-ui.com): the translation from the engine's run log (the endpoint is the server's)."""

from __future__ import annotations

import json

from loamz.engine.agents.kernel.tools.function import tool
from loamz.engine.agents.kernel.tools.protocols import ToolRisk
from loamz.engine.agents.kernel.types.run_log import RunLogEntry, RunLogKind
from loamz.engine.agents.kernel.wire import agui

BODY = {
    "threadId": "t1",
    "runId": "client-run",
    "messages": [{"id": "m1", "role": "user", "content": "2+3?"}],
    "tools": [],
    "context": [],
    "state": {},
    "forwardedProps": {},
}


def entry(seq: int, kind: str, **payload) -> RunLogEntry:
    return RunLogEntry(run_id="r", seq=seq, kind=kind, payload=payload)


def events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


@tool(risk=ToolRisk.SAFE, idempotent=True)
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


# ------------------------------------------------------------------ the translation, as a pure function


def test_text_streams_as_one_message_per_model_turn() -> None:
    t = agui.Translator(thread_id="th", run_id="r1")
    out = t.translate_all(
        [
            entry(0, RunLogKind.TEXT_DELTA, text="Hel"),
            entry(1, RunLogKind.TEXT_DELTA, text="lo"),
            entry(
                2, RunLogKind.ASSISTANT_MESSAGE, text="Hello"
            ),  # the whole turn, already streamed: not repeated
            entry(3, RunLogKind.TEXT_DELTA, text="Bye"),
            entry(4, RunLogKind.ASSISTANT_MESSAGE, text="Bye"),
            entry(5, RunLogKind.RUN_COMPLETED),
        ]
    )
    assert [(e["type"], e.get("delta")) for e in out] == [
        ("TEXT_MESSAGE_START", None),
        ("TEXT_MESSAGE_CONTENT", "Hel"),
        ("TEXT_MESSAGE_CONTENT", "lo"),
        ("TEXT_MESSAGE_END", None),
        ("TEXT_MESSAGE_START", None),
        ("TEXT_MESSAGE_CONTENT", "Bye"),
        ("TEXT_MESSAGE_END", None),
        ("RUN_FINISHED", None),
    ]
    starts = [e["messageId"] for e in out if e["type"] == "TEXT_MESSAGE_START"]
    assert len(set(starts)) == 2  # each turn is its own message
    assert all(e["messageId"] in starts for e in out if e["type"].startswith("TEXT_MESSAGE"))


def test_a_model_that_does_not_stream_still_yields_its_whole_reply() -> None:
    out = agui.Translator(thread_id="th", run_id="r1").translate_all(
        [entry(0, RunLogKind.ASSISTANT_MESSAGE, text="all at once")]
    )
    assert [(e["type"], e.get("delta")) for e in out] == [
        ("TEXT_MESSAGE_START", None),
        ("TEXT_MESSAGE_CONTENT", "all at once"),
        ("TEXT_MESSAGE_END", None),
    ]


def test_a_tool_call_closes_the_open_message_and_has_start_args_end_result() -> None:
    out = agui.Translator(thread_id="th", run_id="r1").translate_all(
        [
            entry(0, RunLogKind.TEXT_DELTA, text="Let me check."),
            entry(
                1,
                RunLogKind.TOOL_CALL,
                call_id="c1",
                tool_name="lookup",
                args={"id": "T-1"},
            ),
            entry(
                2,
                RunLogKind.TOOL_RESULT,
                call_id="c1",
                tool_name="lookup",
                ok=True,
                output="open",
            ),
            entry(
                3,
                RunLogKind.TOOL_RESULT,
                call_id="c2",
                tool_name="lookup",
                ok=False,
                error="boom",
                output="",
            ),
        ]
    )
    assert [e["type"] for e in out] == [
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
        "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT", "TOOL_CALL_RESULT",
    ]  # fmt: skip
    start, args, _end, result, failed = out[3:]
    assert (start["toolCallName"], start["toolCallId"]) == (
        "lookup",
        "c1",
    ) and json.loads(args["delta"]) == {"id": "T-1"}
    assert (result["toolCallId"], result["content"], result["role"]) == (
        "c1",
        "open",
        "tool",
    )
    assert failed["content"] == "error: boom"


def test_failures_and_cancellations_are_run_errors_and_open_messages_are_closed() -> None:
    failed = agui.Translator(thread_id="th", run_id="r1").translate_all(
        [
            entry(0, RunLogKind.TEXT_DELTA, text="x"),
            entry(1, RunLogKind.RUN_FAILED, error="kaput"),
        ]
    )
    assert [e["type"] for e in failed] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_ERROR",
    ]
    assert failed[-1]["message"] == "kaput"
    cancelled = agui.Translator(thread_id="th", run_id="r1").translate_all(
        [entry(0, RunLogKind.RUN_CANCELLED)]
    )
    assert cancelled == [{"type": "RUN_ERROR", "message": "run cancelled", "code": "run_cancelled"}]


def test_approvals_and_subagents_have_their_place_and_the_client_run_id_is_echoed() -> None:
    t = agui.Translator(thread_id="th", run_id="client-run", engine_run_id="engine-run")
    assert t.start() == [{"type": "RUN_STARTED", "threadId": "th", "runId": "client-run"}]
    approval, step_in, step_out, done = t.translate_all(
        [
            entry(
                0,
                RunLogKind.APPROVAL_REQUESTED,
                request_id="q1",
                tool_name="wire",
                args={},
                risk="critical",
            ),
            entry(1, RunLogKind.SUBAGENT_START, agent="researcher"),
            entry(2, RunLogKind.SUBAGENT_DONE, agent="researcher"),
            entry(3, RunLogKind.RUN_COMPLETED),
        ]
    )
    assert approval["type"] == "CUSTOM" and approval["name"] == "loamz.approval_requested"
    assert (
        approval["value"]["loamzRunId"] == "engine-run" and approval["value"]["request_id"] == "q1"
    )
    assert (step_in, step_out) == (
        {"type": "STEP_STARTED", "stepName": "researcher"},
        {"type": "STEP_FINISHED", "stepName": "researcher"},
    )
    assert done == {"type": "RUN_FINISHED", "threadId": "th", "runId": "client-run"}


def test_events_that_have_no_ag_ui_meaning_are_dropped() -> None:
    out = agui.Translator(thread_id="th", run_id="r").translate_all(
        [
            entry(0, RunLogKind.LLM_CALL),
            entry(1, RunLogKind.REASONING_DELTA, text="hmm"),
            entry(2, RunLogKind.USER_MESSAGE, text="hi"),
        ]
    )
    assert out == []


def test_the_prompt_is_the_last_user_message_whatever_its_shape() -> None:
    def prompt(messages):
        return agui.RunAgentInput.model_validate({"threadId": "t", "messages": messages}).prompt()

    assert (
        prompt(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "a"},
                {"role": "user", "content": "last"},
            ]
        )
        == "last"
    )
    assert (
        prompt(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "he"},
                        {"type": "binary", "data": "x"},
                        {"type": "text", "text": "llo"},
                    ],
                }
            ]
        )
        == "hello"
    )
    assert prompt([{"role": "assistant", "content": "only me"}]) is None
    assert prompt([]) is None
    # camelCase in, extra fields ignored
    assert (
        agui.RunAgentInput.model_validate(
            {"threadId": "t", "runId": "r", "forwardedProps": {"x": 1}, "unknown": True}
        ).thread_id
        == "t"
    )


# ------------------------------------------------------------------ the endpoint
