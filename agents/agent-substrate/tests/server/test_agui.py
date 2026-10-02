"""AG-UI (https://docs.ag-ui.com): the translation from the engine's run log, and the endpoint end to end."""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from substrate import ReActAgent, ToolRisk, tool
from substrate.server import agui, create_app
from substrate.testing.scripted import ScriptedModel, ToolCall
from substrate.tools import DurableApproval
from substrate.types import RunLogEntry, RunLogKind

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
    return [
        json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")
    ]


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
    assert all(
        e["messageId"] in starts for e in out if e["type"].startswith("TEXT_MESSAGE")
    )


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


def test_failures_and_cancellations_are_run_errors_and_open_messages_are_closed() -> (
    None
):
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
    assert cancelled == [
        {"type": "RUN_ERROR", "message": "run cancelled", "code": "run_cancelled"}
    ]


def test_approvals_and_subagents_have_their_place_and_the_client_run_id_is_echoed() -> (
    None
):
    t = agui.Translator(thread_id="th", run_id="client-run", engine_run_id="engine-run")
    assert t.start() == [
        {"type": "RUN_STARTED", "threadId": "th", "runId": "client-run"}
    ]
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
    assert (
        approval["type"] == "CUSTOM"
        and approval["name"] == "substrate.approval_requested"
    )
    assert (
        approval["value"]["substrateRunId"] == "engine-run"
        and approval["value"]["request_id"] == "q1"
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
        return agui.RunAgentInput.model_validate(
            {"threadId": "t", "messages": messages}
        ).prompt()

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


def test_agui_streams_a_real_run_with_a_tool_call(tmp_path) -> None:
    agent = ReActAgent(
        "calc",
        model=ScriptedModel(ToolCall("add", {"a": 2, "b": 3}), "It is 5."),
        tools=[add],
    )
    with TestClient(create_app(agent, store=tmp_path)) as client:
        response = client.post("/agui", json=BODY)
    assert response.status_code == 200 and response.headers["content-type"].startswith(
        "text/event-stream"
    )
    out = events(response.text)
    assert [e["type"] for e in out] == [
        "RUN_STARTED", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT",
        "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_FINISHED",
    ]  # fmt: skip
    assert out[0] == {"type": "RUN_STARTED", "threadId": "t1", "runId": "client-run"}
    assert out[4]["content"] == "5" and out[6]["delta"] == "It is 5."
    assert (
        response.headers["x-run-id"] != "client-run"
    )  # the engine's id, for /runs/{id}/…


def test_agui_needs_a_user_message_and_a_thread(tmp_path) -> None:
    with TestClient(
        create_app(ReActAgent("a", model=ScriptedModel("x")), store=tmp_path)
    ) as client:
        assert (
            client.post(
                "/agui",
                json={
                    **BODY,
                    "messages": [{"id": "1", "role": "assistant", "content": "hi"}],
                },
            ).status_code
            == 422
        )
        assert (
            client.post("/agui", json={"messages": BODY["messages"]}).status_code == 422
        )


def test_agui_can_be_left_off(tmp_path) -> None:
    with TestClient(
        create_app(
            ReActAgent("a", model=ScriptedModel("x")), store=tmp_path, agui_path=None
        )
    ) as client:
        assert client.post("/agui", json=BODY).status_code == 404
        assert client.post("/chat", json={"message": "hi"}).status_code == 200


def test_agui_threads_carry_the_conversation_on_the_server(tmp_path) -> None:
    from substrate.context import ContextConfig
    from substrate.stores import Store
    from substrate.types import Role

    store = Store.at(tmp_path)
    agent = ReActAgent(
        "mem",
        model=ScriptedModel(
            lambda messages: (
                "I heard: "
                + " | ".join(m.text for m in messages if m.role == Role.USER)
            )
        ),
        context=ContextConfig(store.threads),
    )
    with TestClient(create_app(agent, store=store)) as client:
        client.post(
            "/agui",
            json={
                **BODY,
                "threadId": "chat",
                "messages": [{"id": "1", "role": "user", "content": "My name is Ada."}],
            },
        )
        # the client resends only the new message; the server remembers the rest
        second = events(
            client.post(
                "/agui",
                json={
                    **BODY,
                    "threadId": "chat",
                    "messages": [{"id": "2", "role": "user", "content": "Who am I?"}],
                },
            ).text
        )
    assert "My name is Ada. | Who am I?" in "".join(e.get("delta", "") for e in second)


async def test_agui_announces_an_approval_and_carries_on_after_the_decision(
    tmp_path,
) -> None:
    @tool(risk=ToolRisk.CRITICAL, idempotent=False)
    def wire(amount: int) -> str:
        """Wire money."""
        return f"wired {amount}"

    agent = ReActAgent(
        "t",
        model=ScriptedModel(ToolCall("wire", {"amount": 5}), "Sent."),
        tools=[wire],
        approval_handler=DurableApproval(),
    )
    app = create_app(agent, store=tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            stream = asyncio.create_task(client.post("/agui", json=BODY))
            run_id, pending = "", []
            for _ in range(200):
                await asyncio.sleep(0.02)
                runs = await app.state.runtime.runs_for_thread("t1")
                if runs:
                    run_id = str(runs[0].run_id)
                    pending = (await client.get(f"/runs/{run_id}/approvals")).json()
                    if pending:
                        break
            assert pending and not stream.done()
            await client.post(
                f"/runs/{run_id}/approvals/{pending[0]['request_id']}",
                json={"decision": "approved"},
            )
            out = events((await asyncio.wait_for(stream, 10)).text)
    custom = next(e for e in out if e["type"] == "CUSTOM")
    assert (
        custom["name"] == "substrate.approval_requested"
        and custom["value"]["substrateRunId"] == run_id
    )
    assert [e["type"] for e in out][-1] == "RUN_FINISHED" and any(
        e.get("delta") == "Sent." for e in out
    )
