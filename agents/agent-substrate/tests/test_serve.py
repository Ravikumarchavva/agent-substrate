"""substrate.serve.add_routes — real FastAPI app, real in-process Runtime,
real stub kernel agent (same style as tests/serving/test_session.py). No
mocks: this drives the actual mounted route through a real TestClient
request and asserts on the real SSE body.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from substrate.agents.runtime.context import RunContext
from substrate.agents.runtime.runtime import Runtime
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import Message
from substrate.serve import add_routes


@dataclass
class ReplyAgent:
    """Same shape as test_session.py's ReplyAgent -- replies to every
    message with a fixed text string, no real LLM call needed."""

    reply: str
    name: str = "reply"

    @property
    def id(self) -> Actor:
        return Actor(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            await ctx._log("text.delta", {"text": self.reply})
            await ctx.reply(msg, {"text": self.reply})


@dataclass
class CrashAgent:
    name: str = "crash"

    @property
    def id(self) -> Actor:
        return Actor(type="agent", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        raise RuntimeError("intentional crash")


def _build_app(agent) -> tuple[FastAPI, Runtime]:
    runtime = Runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        yield
        await runtime.stop()

    app = FastAPI(lifespan=lifespan)
    add_routes(app, runtime, agent, path="/chat")
    return app, runtime


def _sse_events(body: str) -> list[dict]:
    import json

    events = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload == "[DONE]":
            continue
        events.append(json.loads(payload))
    return events


def test_add_routes_streams_a_real_agent_run_end_to_end() -> None:
    agent = ReplyAgent(reply="hello from add_routes", name="e2e_agent")
    app, _runtime = _build_app(agent)

    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "hi"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(resp.text)
    assert events[0]["type"] == "protocol.hello"
    assert events[-1]["type"] == "run.completed"
    assert any(
        e.get("type") == "text.delta" and e.get("text") == "hello from add_routes"
        for e in events
    )
    assert resp.text.rstrip().endswith("data: [DONE]")


def test_add_routes_surfaces_a_real_agent_error_as_run_failed() -> None:
    agent = CrashAgent(name="e2e_crash_agent")
    app, _runtime = _build_app(agent)

    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "hi"})

    events = _sse_events(resp.text)
    assert any(e.get("type") == "run.failed" for e in events)


def test_add_routes_threadless_request_gets_a_generated_correlation_id() -> None:
    """The real bug this guards against: Message.correlation_id has no
    None-means-"generate one" case of its own -- passing thread_id=None
    straight through as correlation_id=None crashes pydantic validation
    before the run ever starts."""
    agent = ReplyAgent(reply="ok", name="threadless_agent")
    app, _runtime = _build_app(agent)

    with TestClient(app) as client:
        resp = client.post("/chat", json={"message": "hi"})

    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert events[-1]["type"] == "run.completed"


def test_add_routes_respects_a_custom_path() -> None:
    agent = ReplyAgent(reply="custom path works", name="custom_path_agent")
    runtime = Runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        yield
        await runtime.stop()

    app = FastAPI(lifespan=lifespan)
    add_routes(app, runtime, agent, path="/my-custom-agent")

    with TestClient(app) as client:
        resp = client.post("/my-custom-agent", json={"message": "hi"})
        missing = client.post("/chat", json={"message": "hi"})

    assert resp.status_code == 200
    assert missing.status_code == 404
