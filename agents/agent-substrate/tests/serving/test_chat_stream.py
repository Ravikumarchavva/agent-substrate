"""GET /stream/{thread_id} — reconnect endpoint for a browser that lost its
original SSE connection while a run kept executing durably server-side.

Route-level coverage only (auth/ownership/no-active-run wiring); the actual
tail/terminal-event logic (the thing that used to crash on any non-streamable
EventLog kind) is covered directly in tests/serving/test_session.py's
tail_wire_events tests.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from substrate.serving.monolith.app import app
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims


from substrate.serving.shared.rate_limit import rate_limit


@pytest.fixture
def mock_user_claims() -> AuthClaims:
    return AuthClaims(sub="stream-test-user", tenant_id="default")


@pytest.fixture(autouse=True)
def override_auth(mock_user_claims: AuthClaims):
    app.dependency_overrides[get_current_user] = lambda: mock_user_claims
    app.dependency_overrides[rate_limit] = lambda: None
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(rate_limit, None)


@pytest.mark.asyncio
async def test_stream_thread_404_for_unknown_thread() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/stream/{uuid.uuid4()}")
            assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_thread_done_immediately_when_no_active_run() -> None:
    """A thread that exists but has no active run (never started, or already
    finished) gets a clean [DONE] — not a hang, not a 500."""
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            create_resp = await client.post("/threads", json={"name": "Reconnect test"})
            assert create_resp.status_code == 201
            thread_id = create_resp.json()["id"]

            resp = await client.get(f"/stream/{thread_id}")
            assert resp.status_code == 200
            assert "data: [DONE]" in resp.text

            del_resp = await client.delete(f"/threads/{thread_id}")
            assert del_resp.status_code in (200, 204)
