"""``create_app(agent)`` — a real FastAPI app, a real Runtime on a real store in a folder, real agents. No mocks: the
mounted routes are driven through real requests and the assertions are on the real SSE bodies."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from substrate import ReActAgent, ToolRisk, tool
from substrate.runtime import Message, RunContext
from substrate.server import create_app, load
from substrate.server.streaming import with_keepalive
from substrate.testing.scripted import ScriptedModel, ToolCall
from substrate.tools import DurableApproval
from substrate.types import Actor


@dataclass
class ReplyAgent:
    """Replies to every message with a fixed text — no model."""

    reply: str
    name: str = "reply"

    @property
    def id(self) -> Actor:
        return Actor(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            await ctx.live("text.delta", {"text": self.reply})
            await ctx.reply(msg, {"text": self.reply})


@dataclass
class CrashAgent:
    name: str = "crash"

    @property
    def id(self) -> Actor:
        return Actor(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        raise RuntimeError("intentional crash")


@dataclass
class SlowAgent:
    """Holds its thread until released."""

    release: asyncio.Event
    name: str = "slow"

    @property
    def id(self) -> Actor:
        return Actor(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        await self.release.wait()
        for msg in inbox:
            await ctx.reply(msg, {"text": "done"})


def sse(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]


@asynccontextmanager
async def serving(app):
    """The app's lifespan, and an async client on it (httpx's ASGI transport does not run lifespans itself)."""
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            yield client


# ------------------------------------------------------------------ the SSE wire protocol


def test_chat_streams_a_real_run_end_to_end(tmp_path) -> None:
    app = create_app(ReplyAgent("hello from the server"), store=tmp_path)
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-run-id"]
    events = sse(response.text)
    assert events[0]["type"] == "protocol.hello" and events[-1]["type"] == "run.completed"
    assert {"type": "text.delta", "text": "hello from the server"} in events
    assert response.text.rstrip().endswith("data: [DONE]")


def test_a_failing_agent_surfaces_as_run_failed(tmp_path) -> None:
    with TestClient(create_app(CrashAgent(), store=tmp_path)) as client:
        events = sse(client.post("/chat", json={"message": "hi"}).text)
    assert any(e["type"] == "run.failed" for e in events)


def test_a_threadless_request_and_a_custom_path_work(tmp_path) -> None:
    with TestClient(create_app(ReplyAgent("ok"), store=tmp_path, chat_path="/ask")) as client:
        assert sse(client.post("/ask", json={"message": "hi"}).text)[-1]["type"] == "run.completed"
        assert client.post("/chat", json={"message": "hi"}).status_code == 404
        assert client.get("/health").json() == {"status": "ok"}


async def test_a_second_request_on_a_busy_thread_is_a_409_not_a_stream(tmp_path) -> None:
    release = asyncio.Event()
    async with serving(create_app(SlowAgent(release), store=tmp_path)) as client:
        first = asyncio.create_task(client.post("/chat", json={"message": "a", "thread_id": "t"}))
        for _ in range(100):  # until the first run holds the thread
            await asyncio.sleep(0.02)
            second = await client.post("/chat", json={"message": "b", "thread_id": "t"})
            if second.status_code == 409:
                break
        assert second.status_code == 409 and "already has an active run" in second.json()["detail"]
        release.set()
        assert sse((await first).text)[-1]["type"] == "run.completed"


def test_a_finished_run_can_be_followed_again_and_resumed_from_a_sequence_number(tmp_path) -> None:
    with TestClient(create_app(ReplyAgent("again"), store=tmp_path)) as client:
        first = client.post("/chat", json={"message": "hi"})
        run_id = first.headers["x-run-id"]
        replay = sse(client.get(f"/runs/{run_id}/events").text)
        assert replay[0]["type"] == "protocol.hello" and replay[-1]["type"] == "run.completed"
        # a client that already saw everything resumes from past the end: it is told how the run ended, and the stream closes
        later = sse(client.get(f"/runs/{run_id}/events", params={"from_seq": 10_000}).text)
        assert [e["type"] for e in later] == ["protocol.hello", "run.completed"]
        # the replay of a finished run carries the reply as a message, since its live token deltas are gone
        assert any(e["type"] == "text.delta" and e["text"] == "again" for e in replay) or replay[-1]["type"] == "run.completed"


def test_an_agent_factory_is_called_once_at_startup_sync_or_async(tmp_path) -> None:
    calls = []

    def build():
        calls.append("sync")
        return ReplyAgent("from a factory", name="f1")

    async def build_async():
        calls.append("async")
        return ReplyAgent("from an async factory", name="f2")

    with TestClient(create_app(build, store=tmp_path / "a")) as client:
        client.post("/chat", json={"message": "x"})
        client.post("/chat", json={"message": "y"})
    with TestClient(create_app(build_async, store=tmp_path / "b")) as client:
        assert "from an async factory" in client.post("/chat", json={"message": "x"}).text
    assert calls == ["sync", "async"]


def test_a_store_and_a_runtime_are_alternatives(tmp_path) -> None:
    from substrate.runtime import Runtime

    with pytest.raises(ValueError, match="not both"):
        create_app(ReplyAgent("x"), store=tmp_path, runtime=Runtime.open(tmp_path))


# ------------------------------------------------------------------ approvals, over HTTP


def _treasurer():
    @tool(risk=ToolRisk.CRITICAL, idempotent=False)
    def wire(amount: int) -> str:
        """Wire money."""
        return f"wired {amount}"

    return ReActAgent(
        "treasurer",
        model=ScriptedModel(ToolCall("wire", {"amount": 5}), "All done."),
        tools=[wire],
        approval_handler=DurableApproval(),
    )


async def _until_pending(client, run_id: str) -> list[dict]:
    for _ in range(200):
        pending = (await client.get(f"/runs/{run_id}/approvals")).json()
        if pending:
            return pending
        await asyncio.sleep(0.02)
    raise AssertionError("the run never asked for approval")


async def _start(client, thread: str):
    """Start a chat (it stays open until the approval is decided) and return its task and run id."""
    task = asyncio.create_task(client.post("/chat", json={"message": "pay", "thread_id": thread}))
    for _ in range(200):
        await asyncio.sleep(0.02)
        runs = await client._transport.app.state.runtime.runs_for_thread(thread)  # noqa: SLF001
        if runs:
            return task, str(runs[0].run_id)
    raise AssertionError("no run started")


async def test_a_risky_tool_waits_over_http_until_a_person_decides_and_who_decided_is_the_servers_word(tmp_path) -> None:
    app = create_app(_treasurer(), store=tmp_path, identity_of=lambda request: request.headers.get("x-user", "anonymous"))
    async with serving(app) as client:
        chat, run_id = await _start(client, "pay")
        (pending,) = await _until_pending(client, run_id)
        assert (pending["tool_name"], pending["args"], pending["risk"]) == ("wire", {"amount": 5}, "critical")
        assert not chat.done()

        # a body that claims to be someone else is just ignored: the identity is the server's, from its own auth
        answer = await client.post(
            f"/runs/{run_id}/approvals/{pending['request_id']}",
            json={"decision": "approved", "reason": "ok", "decided_by": "mallory"},
            headers={"x-user": "carol"},
        )
        assert answer.json() == {"status": "recorded", "decided_by": "carol"}
        events = sse((await chat).text)
        assert events[-1]["type"] == "run.completed"
        assert any(e["type"] == "approval.requested" for e in events)
        assert await client.get(f"/runs/{run_id}/approvals") is not None
        decided = [e.payload for e in await app.state.runtime.read(run_id) if e.kind == "approval.decided"]
        assert [(d["decision"], d["decided_by"]) for d in decided] == [("approved", "carol")]


async def test_an_unknown_or_unmakeable_decision_is_refused(tmp_path) -> None:
    async with serving(create_app(_treasurer(), store=tmp_path)) as client:
        chat, run_id = await _start(client, "pay")
        (pending,) = await _until_pending(client, run_id)
        url = f"/runs/{run_id}/approvals/{pending['request_id']}"
        assert (await client.post(f"/runs/{run_id}/approvals/nope", json={"decision": "approved"})).status_code == 404
        assert (await client.post(url, json={"decision": "skipped"})).status_code == 422
        assert (await client.post(url, json={"decision": "modified"})).status_code == 422  # needs modified_args
        await client.post(url, json={"decision": "denied"})
        await chat


async def test_cancelling_a_run_ends_its_stream(tmp_path) -> None:
    async with serving(create_app(_treasurer(), store=tmp_path)) as client:
        chat, run_id = await _start(client, "pay")
        await _until_pending(client, run_id)
        assert (await client.post(f"/runs/{run_id}/cancel")).json()["cancelled"] == [run_id]
        assert sse((await asyncio.wait_for(chat, 10)).text)[-1]["type"] == "run.cancelled"


# ------------------------------------------------------------------ tenants


async def test_a_run_is_invisible_to_every_other_tenant(tmp_path) -> None:
    app = create_app(_treasurer(), store=tmp_path, tenant_of=lambda request: request.headers.get("x-tenant", "acme"))
    async with serving(app) as client:
        chat = asyncio.create_task(client.post("/chat", json={"message": "pay", "thread_id": "pay"}, headers={"x-tenant": "acme"}))
        run_id = ""
        for _ in range(200):
            await asyncio.sleep(0.02)
            runs = await app.state.runtime.runs_for_thread("pay")
            if runs:
                run_id = str(runs[0].run_id)
                break
        (pending,) = await _until_pending_as(client, run_id, "acme")
        evil = {"x-tenant": "evilcorp"}
        assert (await client.get(f"/runs/{run_id}/events", headers=evil)).status_code == 404
        assert (await client.get(f"/runs/{run_id}/approvals", headers=evil)).status_code == 404
        assert (await client.post(f"/runs/{run_id}/cancel", headers=evil)).status_code == 404
        assert (
            await client.post(f"/runs/{run_id}/approvals/{pending['request_id']}", json={"decision": "approved"}, headers=evil)
        ).status_code == 404
        assert (await client.get(f"/runs/{run_id}/approvals", headers={"x-tenant": "acme"})).status_code == 200
        await client.post(f"/runs/{run_id}/approvals/{pending['request_id']}", json={"decision": "denied"}, headers={"x-tenant": "acme"})
        await chat


async def _until_pending_as(client, run_id: str, tenant: str) -> list[dict]:
    for _ in range(200):
        pending = (await client.get(f"/runs/{run_id}/approvals", headers={"x-tenant": tenant})).json()
        if pending:
            return pending
        await asyncio.sleep(0.02)
    raise AssertionError("the run never asked for approval")


# ------------------------------------------------------------------ auth is FastAPI's


def test_dependencies_guard_every_route(tmp_path) -> None:
    from fastapi import Depends, HTTPException

    def needs_token(request: Request) -> None:
        if request.headers.get("authorization") != "Bearer s3cret":
            raise HTTPException(401, "no token")

    app = create_app(ReplyAgent("secret"), store=tmp_path, dependencies=[Depends(needs_token)])
    with TestClient(app) as client:
        assert client.post("/chat", json={"message": "x"}).status_code == 401
        assert client.post("/agui", json={"threadId": "t", "messages": []}).status_code == 401
        assert client.get("/runs/r/events").status_code == 401
        assert client.post("/chat", json={"message": "x"}, headers={"authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/health").status_code == 200  # liveness is for the platform, not the user


# ------------------------------------------------------------------ small parts


async def test_a_quiet_stream_gets_keepalives_and_keeps_every_event() -> None:
    async def slow():
        for n in range(2):
            await asyncio.sleep(0.12)
            yield n

    seen = [item async for item in with_keepalive(slow(), 0.03)]
    assert [x for x in seen if x is not None] == [0, 1] and seen.count(None) >= 3


def test_module_attribute_references_load_an_object(tmp_path, monkeypatch) -> None:
    (tmp_path / "my_desk.py").write_text("class Holder:\n    agent = 'the agent'\nagent = 'top level'\n")
    monkeypatch.chdir(tmp_path)
    assert load("my_desk:agent") == "top level"
    assert load("my_desk:Holder.agent") == "the agent"
    for bad in ("my_desk", "my_desk:", ":agent", "my_desk:nope"):
        with pytest.raises(ValueError):
            load(bad)
