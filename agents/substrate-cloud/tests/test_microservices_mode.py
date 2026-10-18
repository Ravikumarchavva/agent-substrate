"""What only microservices mode has: the run lifecycle across services, the events that carry it, and the audit trail.

The shared contract (token, threads, chat, files) is in ``test_agent_api_modes.py``, which runs in both modes. Here the services are
exercised through their own APIs and the event bus, with the same in-process wiring (``_modes.py``).
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import httpx2
import pytest
import pytest_asyncio
from _modes import (
    Backend,
    GatedModel,
    eventually,
    in_one_task,
    microservices,
    parse_sse,
)
from _oidc import FakeRealm
from sqlalchemy import select

from substrate_cloud.services.job_controller.models import JobRun
from substrate_cloud.shared.events import types as events

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def environment():
    async with in_one_task(microservices(FakeRealm())) as backend:
        yield backend


@pytest.fixture
def stack(environment: Backend) -> Backend:
    return environment.for_test()


def _refuse_connection(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("down")


async def client_of(stack: Backend, service: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.apps[service]),
        base_url="http://test/api/v1",
    )


async def job_runs(stack: Backend, thread_id: str) -> list[JobRun]:
    factory = stack.apps["job_controller"].state.session_factory
    async with factory() as db:
        rows = await db.execute(
            select(JobRun).where(JobRun.thread_id == uuid.UUID(thread_id))
        )
        return list(rows.scalars())


async def thread(stack: Backend, **who) -> str:
    created = await stack.client.post(
        "/threads", json={"name": "t"}, headers=stack.headers(**who)
    )
    return created.json()["id"]


async def chat(stack: Backend, thread_id: str, **who) -> httpx.Response:
    return await stack.client.post(
        "/chat",
        json={"thread_id": thread_id, "messages": [{"role": "user", "content": "hi"}]},
        headers=stack.headers(**who),
    )


async def audit(stack: Backend, **query) -> list[dict]:
    admin = await client_of(stack, "admin")
    async with admin:
        response = await admin.get(
            "/admin/audit",
            params=query,
            headers=stack.headers("root", roles=["platform_admin"]),
        )
    assert response.status_code == 200, response.text
    return response.json()


# ── a run's lifecycle ────────────────────────────────────────────────────────


async def test_a_finished_run_is_marked_completed_and_audited(stack: Backend):
    thread_id = await thread(stack)
    assert (await chat(stack, thread_id)).status_code == 200

    async def completed():
        runs = await job_runs(stack, thread_id)
        return runs and runs[0].status == "completed" and runs[0]

    run = await eventually(completed)  # workflow.run_completed -> complete_run
    subject, org = stack.who()
    assert (run.tenant_id, run.user_id) == (org, subject)  # taken from the token
    assert run.runtime_run_id and run.completed_at and run.steps_count == 0

    trail = await eventually(
        lambda: _audit_has(
            stack,
            org,
            {events.THREAD_CREATED, events.RUN_STARTED, events.RUN_COMPLETED},
        )
    )
    assert {e["actor_id"] for e in trail} == {subject}
    assert {e["resource_id"] for e in trail if e["action"].startswith("workflow.")} == {
        str(run.id)
    }


async def _audit_has(stack: Backend, org: str, actions: set[str]):
    entries = await audit(stack, tenant_id=org)
    return entries if actions <= {e["action"] for e in entries} else None


async def test_a_run_that_is_cancelled_ends_cancelled_everywhere(stack: Backend):
    model = GatedModel()
    runtime_state = stack.apps["agent_runtime"].state
    original, runtime_state.model_client = runtime_state.model_client, model
    try:
        thread_id = await thread(stack)
        streaming = asyncio.create_task(chat(stack, thread_id))
        await asyncio.wait_for(model.started.wait(), 10)

        # one run at a time: a second message while the first is in flight is refused
        second = await chat(stack, thread_id)
        assert second.status_code == 409 and second.headers["content-type"].startswith(
            "application/problem+json"
        )

        cancelled = await stack.client.post(
            f"/chat/{thread_id}/cancel", headers=stack.headers()
        )
        assert cancelled.json() == {"status": "cancelled", "thread_id": thread_id}

        response = await asyncio.wait_for(
            streaming, 20
        )  # job.cancel_requested reached the runtime and stopped the run
        assert "run.cancelled" in {
            e["type"] for e in parse_sse(response.text) if isinstance(e, dict)
        }
        assert (await job_runs(stack, thread_id))[0].status == "cancelled"
        _, org = stack.who()
        await eventually(lambda: _audit_has(stack, org, {events.RUN_CANCELLED}))
        assert events.CANCEL_REQUESTED in stack.bus.types()
    finally:
        model.gate.set()
        runtime_state.model_client = original


async def test_a_run_that_fails_ends_failed_and_does_not_block_the_thread(
    stack: Backend,
):
    runtime_state = stack.apps["agent_runtime"].state
    original, runtime_state.history = (
        runtime_state.history,
        None,
    )  # the agent cannot read its conversation
    try:
        thread_id = await thread(stack)
        response = await chat(stack, thread_id)
        assert response.status_code == 200
        assert "run.failed" in {
            e["type"] for e in parse_sse(response.text) if isinstance(e, dict)
        }

        async def failed():
            runs = await job_runs(stack, thread_id)
            return (
                runs and runs[0].status == "failed" and runs[0]
            )  # workflow.run_failed -> fail_run

        run = await eventually(failed)
        assert run.error_message
        _, org = stack.who()
        await eventually(lambda: _audit_has(stack, org, {events.RUN_FAILED}))
    finally:
        runtime_state.history = original

    async def next_run_accepted():
        return (await chat(stack, thread_id)).status_code == 200

    await eventually(next_run_accepted)


async def test_a_runtime_that_refuses_the_run_fails_it_at_once(stack: Backend):
    """The runtime answers 5xx: the controller records the run as failed, says so as an event, and the caller gets a problem."""
    jobs = stack.apps["job_controller"].state
    working = jobs.runtime_client
    down = httpx2.MockTransport(lambda request: httpx2.Response(500, text="boom"))
    jobs.runtime_client = type(working)(
        "http://runtime", "agent runtime", transport=down
    )
    try:
        thread_id = await thread(stack)
        response = await chat(stack, thread_id)
    finally:
        jobs.runtime_client = working
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/problem+json")
    assert [r.status for r in await job_runs(stack, thread_id)] == ["failed"]
    _, org = stack.who()
    await eventually(lambda: _audit_has(stack, org, {events.RUN_FAILED}))


# ── identity from the token ──────────────────────────────────────────────────


async def test_a_run_is_visible_to_its_user_and_the_tenants_administrator_only(
    stack: Backend,
):
    thread_id = await thread(stack, sub="alice")
    assert (await chat(stack, thread_id, sub="alice")).status_code == 200
    run = (await job_runs(stack, thread_id))[0]
    jobs = await client_of(stack, "job_controller")
    async with jobs:
        path = f"/jobs/runs/{run.id}"
        assert (await jobs.get(path, headers=stack.headers("alice"))).status_code == 200
        assert (await jobs.get(path, headers=stack.headers("bob"))).status_code == 404
        assert (
            await jobs.get(path, headers=stack.headers("alice", "org-2"))
        ).status_code == 404
        boss = stack.headers("boss", roles=["tenant_admin"])
        assert (await jobs.get(path, headers=boss)).status_code == 200
        elsewhere = stack.headers("boss", "org-2", roles=["tenant_admin"])
        assert (await jobs.get(path, headers=elsewhere)).status_code == 404


async def test_a_service_does_not_take_identity_from_a_request_body(stack: Backend):
    """Starting a run is for the token's user: a body that names another user changes nothing."""
    thread_id = await thread(stack, sub="alice")
    jobs = await client_of(stack, "job_controller")
    async with jobs:
        response = await jobs.post(
            "/jobs/runs",
            json={
                "thread_id": thread_id,
                "user_content": "x",
                "user_id": "bob",
                "tenant_id": "org-9",
            },
            headers=stack.headers("bob"),  # bob does not own alice's thread
        )
    assert response.status_code == 404
    assert await job_runs(stack, thread_id) == []


async def test_an_administrator_of_another_tenant_cannot_reach_a_thread(stack: Backend):
    thread_id = await thread(stack, sub="alice")
    other_admin = stack.headers("boss", "org-2", roles=["tenant_admin"])
    assert (
        await stack.client.get(f"/threads/{thread_id}", headers=other_admin)
    ).status_code == 404


async def test_only_a_platform_administrator_reads_the_audit_trail(stack: Backend):
    admin = await client_of(stack, "admin")
    async with admin:
        for who in (
            stack.headers("alice"),
            stack.headers("boss", roles=["tenant_admin"]),
        ):
            assert (await admin.get("/admin/audit", headers=who)).status_code == 403


# ── the gateway between clients and services ─────────────────────────────────


async def test_a_policy_service_that_is_down_refuses_the_chat(stack: Backend):
    gateway = stack.apps["gateway"].state
    working = gateway.policy_client
    down = httpx2.MockTransport(_refuse_connection)
    gateway.policy_client = type(working)("http://policy", "policy", transport=down)
    try:
        thread_id = await thread(stack)
        response = await chat(stack, thread_id)
    finally:
        gateway.policy_client = working
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/problem+json")
    assert await job_runs(stack, thread_id) == []  # nothing was started


async def test_a_denying_policy_refuses_the_chat(stack: Backend):
    policy = await client_of(stack, "policy")
    async with policy:
        rule = await policy.post(
            "/policy/rules",
            json={
                "role": "end_user",
                "action": "submit_conversation",
                "effect": "deny",
                "priority": 10,
            },
            headers=stack.headers("root", roles=["platform_admin"]),
        )
        assert rule.status_code == 201
    thread_id = await thread(stack)
    response = await chat(stack, thread_id)
    assert response.status_code == 403
    assert await job_runs(stack, thread_id) == []


async def test_a_conversation_service_that_is_down_is_a_502_problem(stack: Backend):
    gateway = stack.apps["gateway"].state
    working = gateway.conversation_client
    down = httpx2.MockTransport(_refuse_connection)
    gateway.conversation_client = type(working)(
        "http://conversation", "conversation", transport=down
    )
    try:
        response = await stack.client.get("/threads", headers=stack.headers())
    finally:
        gateway.conversation_client = working
    assert response.status_code == 502
    assert response.json()["status"] == 502


# ── files from the code interpreter ──────────────────────────────────────────


async def test_code_interpreter_files_are_saved_for_the_caller_and_downloadable(
    stack: Backend,
):
    from substrate_cloud.services.tool_executor.outputs import save_file_outputs
    from substrate_cloud.shared.auth.claims import AuthClaims

    subject, org = stack.who()
    thread_id = uuid.UUID(await thread(stack))
    result = {
        "tool_name": "code_interpreter",
        "tool_call_id": "c1",
        "content": "done",
        "is_error": False,
        "metadata": {
            "outputs": [
                {"type": "text", "content": "plain"},
                {
                    "type": "file",
                    "name": "plot.csv",
                    "content": "a,b\n1,2\n",
                    "encoding": "utf-8",
                },
            ]
        },
    }
    saved = await save_file_outputs(
        result,
        file_access=stack.apps["gateway"].state.file_access,
        user=AuthClaims(sub=subject, tenant_id=org),
        thread_id=thread_id,
    )
    ref = saved["metadata"]["saved_files"][0]
    assert ref["name"] == "plot.csv" and ref["file_id"] in saved["content"]

    mine = await stack.client.get(
        f"/files/{ref['file_id']}/download", headers=stack.headers()
    )
    assert mine.status_code == 200 and mine.content == b"a,b\n1,2\n"
    theirs = await stack.client.get(
        f"/files/{ref['file_id']}/download", headers=stack.headers("bob")
    )
    assert theirs.status_code == 404
    listed = await stack.client.get(
        "/files/documents",
        params={"thread_id": str(thread_id)},
        headers=stack.headers(),
    )
    assert [d["name"] for d in listed.json()] == ["plot.csv"]
