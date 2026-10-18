"""The Agent API in both modes, in one process and without Docker, behind one interface.

``monolith``       the real monolith app and its lifespan (local PostgreSQL for its tables, a folder store), with a scripted model.
``microservices``  the real gateway and the real services, wired to each other over ASGI instead of HTTP: SQLite for their tables, one
                   folder store (runtime journal and files), an in-memory event bus, a scripted model. Their background consumers run.

``Backend.client`` speaks the public ``/api/v1`` API; a test written against it must pass in both.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import httpx2
import pytest
from _events import MemoryEventBus
from _oidc import FakeRealm
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from substrate.runtime import Runtime
from substrate.stores import Store
from substrate.testing.scripted import ScriptedModel

from substrate_cloud.file_access import FileAccess
from substrate_cloud.shared.database.base import ServiceBase
from substrate_cloud.shared.service_client import ServiceClient

REPLY = "Hello from the scripted model."


class FakeChatModel(ScriptedModel):
    """The scripted model, shaped like the client the monolith expects (it replaces a client whose provider or name differs)."""

    provider = "openai"

    def __init__(self, *replies: Any) -> None:
        super().__init__(*(replies or (REPLY,)))
        self.model = "gpt-4o"


@dataclass
class Backend:
    mode: str
    client: httpx.AsyncClient
    realm: FakeRealm
    apps: dict[str, Any] = field(default_factory=dict)
    bus: MemoryEventBus | None = None
    # Names are suffixed per test so one test never sees the rows another (or an earlier run) left in a shared database.
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    nonces: set[str] = field(default_factory=set)

    def for_test(self) -> Backend:
        """The same running environment, with identities of its own."""
        fresh = dataclasses.replace(self, nonce=uuid.uuid4().hex[:8])
        self.nonces.add(fresh.nonce)
        return fresh

    def who(
        self, sub: str = "alice", org: str | None = "org-1"
    ) -> tuple[str, str | None]:
        """The subject and organization ids the token for ``sub`` of ``org`` carries."""
        return f"{sub}-{self.nonce}", None if org is None else f"{org}-{self.nonce}"

    def headers(
        self, sub: str = "alice", org: str | None = "org-1", **claims: Any
    ) -> dict[str, str]:
        subject, organization = self.who(sub, org)
        return self.realm.bearer(subject, org=organization, **claims)


def parse_sse(text: str) -> list[Any]:
    """The ``data:`` payloads of an event stream, JSON-decoded where they are JSON (``[DONE]`` stays a string)."""
    events: list[Any] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                raw = line[len("data: ") :]
                try:
                    events.append(json.loads(raw))
                except ValueError:
                    events.append(raw)
    return events


async def eventually(check, *, timeout: float = 10.0, interval: float = 0.05) -> Any:
    """Poll ``check`` (sync or async) until it returns something truthy; fail with its last answer after ``timeout``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        found = check()
        if asyncio.iscoroutine(found):
            found = await found
        if found:
            return found
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"not true after {timeout}s (last answer: {found!r})")
        await asyncio.sleep(interval)


@asynccontextmanager
async def in_one_task(context: Any) -> AsyncIterator[Any]:
    """Enter and leave an async context manager inside a single task.

    pytest-asyncio runs a fixture's setup and teardown in different tasks, which the task groups and cancel scopes under the runtime's
    and the app's lifespans do not allow; here the context lives in its own task for the whole test.
    """
    ready: asyncio.Future = asyncio.get_running_loop().create_future()
    stop = asyncio.Event()

    async def hold() -> None:
        try:
            async with context as value:
                ready.set_result(value)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise

    task = asyncio.create_task(hold())
    try:
        yield await ready
    finally:
        stop.set()
        await task


# ── microservices ────────────────────────────────────────────────────────────


@asynccontextmanager
async def microservices(realm: FakeRealm) -> AsyncIterator[Backend]:
    from substrate_cloud.services.admin import app as admin
    from substrate_cloud.services.admin.service import consume_audit
    from substrate_cloud.services.agent_runtime import app as runtime_app
    from substrate_cloud.services.conversation import app as conversation
    from substrate_cloud.services.gateway import app as gateway
    from substrate_cloud.services.human_gate import app as human_gate
    from substrate_cloud.services.job_controller import app as jobs
    from substrate_cloud.services.job_controller.service import consume_outcomes
    from substrate_cloud.services.live_stream import app as live_stream
    from substrate_cloud.services.policy import app as policy
    from substrate_cloud.services.policy.service import seed_default_policies

    folder = tempfile.mkdtemp(prefix="substrate-services-")
    store = Store.at(folder, file_quota_bytes=10**9)
    # A file, not memory: every session gets its own connection, as against PostgreSQL (the audit and outcome consumers write while
    # requests are being served).
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{folder}/services.db", connect_args={"timeout": 30}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _wal(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as conn:
        await conn.run_sync(ServiceBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    bus = MemoryEventBus()

    apps = {
        "admin": admin.app,
        "agent_runtime": runtime_app.app,
        "conversation": conversation.app,
        "gateway": gateway.app,
        "human_gate": human_gate.app,
        "job_controller": jobs.app,
        "live_stream": live_stream.app,
        "policy": policy.app,
    }
    verifier = realm.verifier()
    # The apps are module-level singletons shared with other tests: what this sets, it removes again.
    touched: list[tuple[Any, str]] = []

    def put(app: Any, name: str, value: Any) -> None:
        setattr(app.state, name, value)
        touched.append((app, name))

    for app in apps.values():
        put(app, "token_verifier", verifier)

    def link(name: str) -> ServiceClient:
        return ServiceClient(
            "http://" + name,
            name,
            transport=httpx2.ASGITransport(app=apps[name], raise_app_exceptions=False),
        )

    links = {
        n: link(n)
        for n in (
            "conversation",
            "policy",
            "job_controller",
            "human_gate",
            "live_stream",
            "agent_runtime",
        )
    }

    async with Runtime(store) as runtime:
        # the services, each with what its lifespan would give it
        for name in ("conversation", "live_stream", "human_gate", "agent_runtime"):
            put(apps[name], "runtime", runtime)
        put(conversation.app, "store", store)
        for name in ("conversation", "job_controller", "agent_runtime", "admin"):
            put(apps[name], "event_bus", bus)
        for name in ("conversation", "job_controller", "policy", "admin"):
            put(apps[name], "session_factory", sessions)
        async with sessions() as db:
            await seed_default_policies(db)
            await db.commit()

        put(runtime_app.app, "history", store.threads)
        put(runtime_app.app, "model_client", FakeChatModel())
        put(runtime_app.app, "tools", [])
        put(runtime_app.app, "system_instructions", "You are a test assistant.")
        put(runtime_app.app, "outcome_tasks", set())
        for name in ("job_controller", "live_stream", "human_gate"):
            put(apps[name], "conversation_client", links["conversation"])
        put(jobs.app, "runtime_client", links["agent_runtime"])
        for name in (
            "conversation",
            "policy",
            "job_controller",
            "human_gate",
            "live_stream",
        ):
            put(gateway.app, f"{name}_client", links[name])
        put(gateway.app, "file_access", FileAccess(local=store.files))

        background = [
            asyncio.create_task(consume_outcomes(jobs.app)),
            asyncio.create_task(runtime_app.consume_cancellations(runtime_app.app)),
            asyncio.create_task(consume_audit(admin.app)),
        ]
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway.app),
            base_url="http://test/api/v1",
        )
        try:
            yield Backend("microservices", client, realm, apps, bus)
        finally:
            await client.aclose()
            for task in [*background, *runtime_app.app.state.outcome_tasks]:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            for link_ in links.values():
                await link_.aclose()
            for app, name in touched:
                if hasattr(app.state, name):
                    delattr(app.state, name)
    await store.aclose()
    await engine.dispose()


# ── monolith ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def monolith(
    realm: FakeRealm, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Backend]:
    from substrate_cloud import factory
    from substrate_cloud.monolith import app as monolith_app
    from substrate_cloud.shared.rate_limit import rate_limit
    from substrate_cloud.shared.settings import settings

    folder = tempfile.mkdtemp(prefix="substrate-monolith-")
    monkeypatch.setattr(settings, "STORE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORE_PATH", folder)
    monkeypatch.setattr(settings, "DATA_SERVICE_URL", "")
    model = FakeChatModel()
    monkeypatch.setattr(
        monolith_app,
        "init_llm_clients",
        lambda cfg: factory.ChatModels(
            api_keys={"openai": "sk-test"},
            model_client_kwargs={},
            model_client=model,
            chat_model="gpt-4o",
        ),
    )
    app = monolith_app.app
    app.dependency_overrides[rate_limit] = lambda: None
    try:
        async with app.router.lifespan_context(app):
            app.state.token_verifier = realm.verifier()
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
            )
            backend = Backend("monolith", client, realm, {"monolith": app})
            try:
                yield backend
            finally:
                await client.aclose()
                async with (
                    app.state.engine.begin() as conn
                ):  # leave the shared database as it was
                    for nonce in backend.nonces:
                        await conn.execute(
                            text("DELETE FROM threads WHERE tenant_id LIKE :n"),
                            {"n": f"%-{nonce}"},
                        )
                app.state.token_verifier = None
    finally:
        app.dependency_overrides.pop(rate_limit, None)


class GatedModel(FakeChatModel):
    """A model that does not answer until ``gate`` is opened, so a test can act while a run is in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def generate_stream(self, *args: Any, **kwargs: Any):
        self.started.set()
        await self.gate.wait()
        async for item in super().generate_stream(*args, **kwargs):
            yield item

    async def generate(self, *args: Any, **kwargs: Any):
        self.started.set()
        await self.gate.wait()
        return await super().generate(*args, **kwargs)
