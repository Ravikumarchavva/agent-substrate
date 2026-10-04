"""``/me/preferences``: standing instructions, timezone and model defaults kept with the account. Real Postgres, throwaway tenant."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.routes.preferences import instructions_for
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims


@asynccontextmanager
async def session():
    async with app.router.lifespan_context(app):
        tenant = f"preftest-{uuid.uuid4().hex[:10]}"
        app.dependency_overrides[get_current_user] = lambda: AuthClaims(
            sub="u1", tenant_id=tenant
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                c.tenant = tenant
                yield c
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            async with app.state.session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
                await db.execute(
                    text("DELETE FROM user_preferences WHERE tenant_id = :t"),
                    {"t": tenant},
                )
                await db.commit()


@pytest.mark.requires_postgres
async def test_preferences_start_empty_and_are_saved_and_replaced():
    async with session() as c:
        assert (await c.get("/me/preferences")).json() == {
            "custom_instructions": "",
            "timezone": "",
            "models": {},
        }
        saved = {
            "custom_instructions": "  Answer briefly.  ",
            "timezone": "Europe/London",
            "models": {"chat_model": "openai/gpt-5.4-mini"},
        }
        assert (await c.put("/me/preferences", json=saved)).status_code == 200
        got = (await c.get("/me/preferences")).json()
        assert got["custom_instructions"] == "Answer briefly."
        assert got["timezone"] == "Europe/London"
        assert got["models"] == {"chat_model": "openai/gpt-5.4-mini"}

        await c.put("/me/preferences", json={"timezone": "Asia/Tokyo"})
        got = (await c.get("/me/preferences")).json()
        assert got["timezone"] == "Asia/Tokyo" and got["custom_instructions"] == ""


@pytest.mark.requires_postgres
async def test_a_bad_timezone_or_an_oversize_instruction_is_refused_with_a_reason():
    async with session() as c:
        r = await c.put("/me/preferences", json={"timezone": "Mars/Olympus"})
        assert r.status_code == 422 and "timezone" in r.text.lower()
        r = await c.put("/me/preferences", json={"custom_instructions": "x" * 2001})
        assert r.status_code == 422


@pytest.mark.requires_postgres
async def test_one_user_never_sees_another_users_preferences():
    async with session() as c:
        await c.put("/me/preferences", json={"custom_instructions": "mine"})
        app.dependency_overrides[get_current_user] = lambda: AuthClaims(
            sub="u2", tenant_id=c.tenant
        )
        assert (await c.get("/me/preferences")).json()["custom_instructions"] == ""


def test_the_assistant_gets_the_timezone_note_then_the_users_own_words():
    prefs = SimpleNamespace(
        timezone="Europe/London", custom_instructions="  Be brief. "
    )
    out = instructions_for(prefs)
    assert out.startswith("User timezone: Europe/London.") and out.endswith("Be brief.")
    assert instructions_for(None) == ""
    assert (
        instructions_for(SimpleNamespace(timezone=None, custom_instructions=None)) == ""
    )
