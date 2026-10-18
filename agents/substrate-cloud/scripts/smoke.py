"""A docker-free smoke test of the Agent API in either deployment mode.

    uv run --package substrate-cloud python agents/substrate-cloud/scripts/smoke.py [microservices|monolith|both]

Starts the real gateway and services (or the real monolith) in this process, wired over ASGI with a scripted model, a folder store and
an in-memory event bus (the monolith also needs the local PostgreSQL its tables live in), and walks the key paths: a request without
a token is refused, a thread is made, a chat run streams its answer and lands in the conversation, a file goes up and comes back, and
someone else sees none of it. Prints one line per step and exits non-zero on the first failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

for key, value in {
    "OIDC_ISSUER": "http://keycloak.test/realms/substrate",
    "OIDC_INSECURE_HTTP": "true",
    "OPENAI_API_KEY": "sk-test-not-a-real-key",
    "ENABLE_TEXT_SAFETY_GUARD": "false",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "SCHEDULER_CATCH_UP": "false",
    "OTEL_SDK_DISABLED": "true",
    "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
}.items():
    os.environ.setdefault(key, value)
logging.disable(logging.INFO)  # the steps are the output
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import pytest  # noqa: E402
from _modes import (  # noqa: E402
    REPLY,
    Backend,
    in_one_task,
    microservices,
    monolith,
    parse_sse,
)
from _oidc import FakeRealm  # noqa: E402


class Failed(Exception):
    pass


def check(ok: bool, what: str, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        raise Failed(f"{what} {detail}".strip())


async def walk(api: Backend) -> None:
    c, me, other = api.client, api.headers("alice"), api.headers("mallory")

    no_token = await c.get("/threads")
    check(
        no_token.status_code == 401
        and no_token.headers["content-type"].startswith("application/problem+json"),
        "a request without a token is refused with problem+json",
    )

    created = await c.post("/threads", json={"name": "smoke"}, headers=me)
    check(created.status_code == 201, "a thread is created", created.text)
    thread_id = created.json()["id"]
    check(
        (await c.get(f"/threads/{thread_id}", headers=other)).status_code == 404,
        "another user does not find it",
    )

    run = await c.post(
        "/chat",
        json={"thread_id": thread_id, "messages": [{"role": "user", "content": "hi"}]},
        headers=me,
    )
    events = parse_sse(run.text)
    text = "".join(e.get("text", "") for e in events if isinstance(e, dict))
    check(
        run.status_code == 200
        and events[-1] == "[DONE]"
        and any(isinstance(e, dict) and e["type"] == "run.completed" for e in events),
        "a chat run streams to completion",
        run.text[:200],
    )
    check(REPLY in text, "the model's answer is in the stream")
    history = (await c.get(f"/threads/{thread_id}/messages", headers=me)).json()
    check(
        any(m["type"] == "user.message" for m in history) and REPLY in str(history),
        "the run is the conversation's history",
    )

    up = await c.post(
        "/files/upload", files={"file": ("a.txt", b"smoke", "text/plain")}, headers=me
    )
    check(up.status_code == 201, "a file is uploaded", up.text)
    file_id = up.json()["id"]
    down = await c.get(f"/files/{file_id}/download", headers=me)
    check(down.content == b"smoke", "it comes back as it went")
    check(
        (await c.get(f"/files/{file_id}/download", headers=other)).status_code == 404,
        "another user does not find the file",
    )
    check(
        (await c.delete(f"/files/{file_id}", headers=me)).status_code == 204,
        "it is deleted",
    )
    check(
        (await c.delete(f"/threads/{thread_id}", headers=me)).status_code == 204,
        "the thread is deleted",
    )


async def run(mode: str) -> None:
    print(mode)
    realm = FakeRealm()
    with pytest.MonkeyPatch.context() as patch:
        running = monolith(realm, patch) if mode == "monolith" else microservices(realm)
        async with in_one_task(running) as backend:
            await walk(backend.for_test())


async def main(which: str) -> int:
    failed = 0
    for mode in ("microservices", "monolith") if which == "both" else (which,):
        try:
            await run(mode)
        except Failed as exc:
            print(f"FAILED in {mode}: {exc}")
            failed += 1
        except (
            Exception
        ) as exc:  # the monolith needs PostgreSQL; say so rather than a stack trace
            if mode == "monolith" and "5432" in str(exc):
                print(
                    f"skipped {mode}: PostgreSQL is not reachable ({exc.__class__.__name__})"
                )
            else:
                raise
    return 1 if failed else 0


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which not in {"microservices", "monolith", "both"}:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(which)))
