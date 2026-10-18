"""The Agent API contract, in both deployment modes.

Every test here runs twice, against the monolith and against the microservices behind the gateway (``_modes.py``), and must pass in
both: that is what "microservices mode serves the monolith's contract" means. The key paths: a request without a verified token is
refused, threads belong to their owner, a chat run starts and streams and lands in the conversation, files belong to their owner.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from _modes import (
    REPLY,
    Backend,
    eventually,
    in_one_task,
    microservices,
    monolith,
    parse_sse,
)
from _oidc import FakeRealm

# The monolith keeps its tables in PostgreSQL. Each mode's environment is built once per module (one event loop for all of them).
pytestmark = [
    pytest.mark.requires_postgres,
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest_asyncio.fixture(
    scope="module", loop_scope="module", params=["monolith", "microservices"]
)
async def environment(request):
    realm = FakeRealm()
    with pytest.MonkeyPatch.context() as patch:
        running = (
            monolith(realm, patch)
            if request.param == "monolith"
            else microservices(realm)
        )
        async with in_one_task(running) as backend:
            yield backend


@pytest.fixture
def api(environment: Backend) -> Backend:
    return environment.for_test()


def is_problem(response: httpx.Response, status: int) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == status and body["detail"] and body["type"]
    return body


async def new_thread(api: Backend, **who) -> str:
    created = await api.client.post(
        "/threads", json={"name": "t"}, headers=api.headers(**who)
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def chat(api: Backend, thread_id: str, text: str = "hi", **who) -> httpx.Response:
    return await api.client.post(
        "/chat",
        json={"thread_id": thread_id, "messages": [{"role": "user", "content": text}]},
        headers=api.headers(**who),
    )


# ── authentication ───────────────────────────────────────────────────────────

SOME_ID = "00000000-0000-4000-8000-000000000001"  # fixed, so the test ids are the same in every xdist worker

PROTECTED = [
    ("GET", "/threads"),
    ("POST", "/threads"),
    ("GET", f"/threads/{SOME_ID}"),
    ("GET", f"/threads/{SOME_ID}/messages"),
    ("POST", "/chat"),
    ("POST", f"/chat/{SOME_ID}/cancel"),
    ("GET", f"/stream/{SOME_ID}"),
    ("GET", f"/hitl/status/{SOME_ID}"),
    ("POST", "/files/upload"),
    ("GET", f"/files/{SOME_ID}/download"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_a_request_without_a_token_is_refused(api: Backend, method, path):
    response = await api.client.request(method, path)
    is_problem(response, 401)
    assert "Bearer" in response.headers["www-authenticate"]


async def test_tokens_that_are_not_the_realms_are_refused(api: Backend):
    for token in (
        api.realm.hs256(),  # the retired shared-secret scheme
        api.realm.token(aud="someone-else"),
        api.realm.token(typ="ID"),
        api.realm.token(lifetime=-60),
    ):
        response = await api.client.get(
            "/threads", headers={"Authorization": f"Bearer {token}"}
        )
        is_problem(response, 401)


async def test_a_token_without_an_organization_is_forbidden(api: Backend):
    response = await api.client.get("/threads", headers=api.headers(org=None))
    is_problem(response, 403)


# ── threads ──────────────────────────────────────────────────────────────────


async def test_a_thread_is_created_listed_renamed_and_deleted_by_its_owner(
    api: Backend,
):
    mine = api.headers()
    created = await api.client.post("/threads", json={"name": "Plans"}, headers=mine)
    assert created.status_code == 201
    thread = created.json()
    assert thread["name"] == "Plans" and thread["message_count"] == 0
    assert {"id", "created_at", "updated_at", "tags", "metadata"} <= set(thread)

    listed = await api.client.get("/threads", headers=mine)
    assert [t["id"] for t in listed.json()] == [thread["id"]]

    renamed = await api.client.patch(
        f"/threads/{thread['id']}", json={"name": "Done"}, headers=mine
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Done"
    assert (await api.client.get(f"/threads/{thread['id']}", headers=mine)).json()[
        "name"
    ] == "Done"

    assert (
        await api.client.delete(f"/threads/{thread['id']}", headers=mine)
    ).status_code == 204
    is_problem(await api.client.get(f"/threads/{thread['id']}", headers=mine), 404)
    assert (await api.client.get("/threads", headers=mine)).json() == []


async def test_a_thread_belongs_to_the_token_not_to_the_request_body(api: Backend):
    """Whoever the body claims to be, the thread is the caller's: another user in the same organization cannot see it."""
    created = await api.client.post(
        "/threads",
        json={
            "name": "mine",
            "user_id": str(uuid.uuid4()),
            "tenant_id": "someone-else",
        },
        headers=api.headers("alice"),
    )
    assert created.status_code == 201
    thread_id = created.json()["id"]
    assert [
        t["id"]
        for t in (await api.client.get("/threads", headers=api.headers("alice"))).json()
    ] == [thread_id]
    assert (await api.client.get("/threads", headers=api.headers("bob"))).json() == []


async def test_someone_elses_thread_is_not_found_whatever_is_asked(api: Backend):
    thread_id = await new_thread(api, sub="alice", org="org-1")
    for stranger in ({"sub": "bob", "org": "org-1"}, {"sub": "alice", "org": "org-2"}):
        who = api.headers(**stranger)
        is_problem(await api.client.get(f"/threads/{thread_id}", headers=who), 404)
        is_problem(
            await api.client.patch(
                f"/threads/{thread_id}", json={"name": "x"}, headers=who
            ),
            404,
        )
        is_problem(await api.client.delete(f"/threads/{thread_id}", headers=who), 404)
        is_problem(
            await api.client.get(f"/threads/{thread_id}/messages", headers=who), 404
        )
        is_problem(await api.client.post(f"/chat/{thread_id}/cancel", headers=who), 404)
        is_problem(await api.client.get(f"/hitl/status/{thread_id}", headers=who), 404)
        is_problem(await chat(api, thread_id, **stranger), 404)
    # the owner still has it
    assert (
        await api.client.get(f"/threads/{thread_id}", headers=api.headers())
    ).status_code == 200


async def test_a_bad_thread_id_is_a_validation_problem(api: Backend):
    response = await api.client.get("/threads/not-a-uuid", headers=api.headers())
    assert is_problem(response, 422)["type"].endswith("invalid-request")


# ── chat ─────────────────────────────────────────────────────────────────────


async def test_a_chat_run_streams_the_answer_and_lands_in_the_conversation(
    api: Backend,
):
    thread_id = await new_thread(api)
    response = await chat(api, thread_id, "say hello")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    assert events[-1] == "[DONE]"
    types = [e["type"] for e in events if isinstance(e, dict)]
    assert "run.completed" in types
    assert REPLY in "".join(
        e.get("text", "")
        for e in events
        if isinstance(e, dict) and e["type"].startswith("text")
    )

    # the same run is the conversation's history
    messages = await api.client.get(
        f"/threads/{thread_id}/messages", headers=api.headers()
    )
    assert messages.status_code == 200
    history = messages.json()
    assert "user.message" in {m["type"] for m in history}
    assert any(REPLY in str(m) for m in history)

    listed = (await api.client.get("/threads", headers=api.headers())).json()
    assert listed[0]["message_count"] >= 2


async def test_a_chat_needs_a_message_and_a_thread_id(api: Backend):
    thread_id = await new_thread(api)
    empty = await api.client.post(
        "/chat", json={"thread_id": thread_id, "messages": []}, headers=api.headers()
    )
    is_problem(empty, 422)
    missing = await api.client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "x"}]},
        headers=api.headers(),
    )
    is_problem(missing, 422)


async def test_after_a_run_there_is_nothing_to_reconnect_to_or_cancel_or_approve(
    api: Backend,
):
    thread_id = await new_thread(api)
    assert (await chat(api, thread_id)).status_code == 200
    who = api.headers()

    reconnect = await api.client.get(f"/stream/{thread_id}", headers=who)
    assert reconnect.status_code == 200 and parse_sse(reconnect.text) == ["[DONE]"]

    async def idle() -> bool:  # the run has ended once its end is recorded
        cancelled = await api.client.post(f"/chat/{thread_id}/cancel", headers=who)
        return (
            cancelled.status_code == 200 and cancelled.json()["status"] == "not_found"
        )

    await eventually(idle)
    assert (await api.client.get(f"/hitl/status/{thread_id}", headers=who)).json() == {
        "pending": []
    }
    is_problem(
        await api.client.post(
            f"/chat/respond/{uuid.uuid4()}", json={"action": "approve"}, headers=who
        ),
        404,
    )


async def test_a_second_message_after_the_first_run_is_accepted(api: Backend):
    thread_id = await new_thread(api)
    assert (await chat(api, thread_id, "one")).status_code == 200

    async def second() -> bool:
        return (await chat(api, thread_id, "two")).status_code == 200

    await eventually(
        second
    )  # microservices record the end of a run a moment after the stream closes
    history = (
        await api.client.get(f"/threads/{thread_id}/messages", headers=api.headers())
    ).json()
    assert sum(1 for m in history if m["type"] == "user.message") == 2


# ── files ────────────────────────────────────────────────────────────────────


async def upload(api: Backend, content: bytes = b"hello file", name="note.txt", **who):
    return await api.client.post(
        "/files/upload",
        files={"file": (name, content, "text/plain")},
        data={k: v for k, v in who.pop("data", {}).items()},
        headers=api.headers(**who),
    )


async def test_a_file_is_uploaded_downloaded_and_deleted_by_its_owner(api: Backend):
    uploaded = await upload(api, b"hello file")
    assert uploaded.status_code == 201, uploaded.text
    meta = uploaded.json()
    assert (
        meta["name"] == "note.txt"
        and meta["size"] == 10
        and meta["mime"] == "text/plain"
    )

    mine = api.headers()
    download = await api.client.get(f"/files/{meta['id']}/download", headers=mine)
    assert download.status_code == 200 and download.content == b"hello file"
    assert "note.txt" in download.headers["content-disposition"]
    assert (await api.client.get(f"/files/{meta['id']}/url", headers=mine)).json()[
        "url"
    ]

    assert (
        await api.client.delete(f"/files/{meta['id']}", headers=mine)
    ).status_code == 204
    is_problem(await api.client.get(f"/files/{meta['id']}/download", headers=mine), 404)


async def test_a_file_is_found_only_by_its_owner(api: Backend):
    meta = (await upload(api, b"secret", sub="alice")).json()
    for stranger in ({"sub": "bob", "org": "org-1"}, {"sub": "alice", "org": "org-2"}):
        who = api.headers(**stranger)
        is_problem(
            await api.client.get(f"/files/{meta['id']}/download", headers=who), 404
        )
        is_problem(await api.client.get(f"/files/{meta['id']}/url", headers=who), 404)
        is_problem(await api.client.delete(f"/files/{meta['id']}", headers=who), 404)
    assert (
        await api.client.get(f"/files/{meta['id']}/download", headers=api.headers())
    ).content == b"secret"


async def test_a_file_can_be_attached_to_the_owners_thread_only(api: Backend):
    mine = await new_thread(api, sub="alice")
    theirs = await new_thread(api, sub="bob")
    attached = await upload(api, sub="alice", data={"thread_id": mine})
    assert attached.status_code == 201 and attached.json()["thread_id"] == mine
    is_problem(await upload(api, sub="alice", data={"thread_id": theirs}), 404)
