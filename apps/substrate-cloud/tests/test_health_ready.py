"""``/health/ready``: reports each dependency, and 503 when a required one is down, without saying why beyond "unavailable"."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from substrate_cloud.monolith.routes.health import router


class _Session:
    def __init__(self, fail=False):
        self.fail = fail

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError(
                "password authentication failed for user postgres at 10.0.0.4"
            )
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_):
        return None


class _Store:
    def __init__(self, fail=False):
        self.fail = fail

    async def run(self, fn):
        if self.fail:
            raise OSError("disk gone")

        class Tx:
            async def fetchall(self, *_):
                return []

        return await fn(Tx())


class _Redis:
    async def ping(self):
        return True


def app_with(db_fail=False, store_fail=False, scheduler=object(), redis=None):
    app = FastAPI()
    app.include_router(router)
    app.state.session_factory = lambda: _Session(db_fail)
    app.state.store = _Store(store_fail)
    app.state.trigger_scheduler = SimpleNamespace(_scheduler=scheduler)
    if redis is not None:
        app.state.redis = redis
    return app


async def get(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/health/ready")


async def test_everything_up_is_ready_and_each_part_is_reported():
    r = await get(app_with(redis=_Redis()))
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert {c["name"] for c in r.json()["checks"]} == {
        "database",
        "store",
        "redis",
        "scheduler",
    }
    assert all(c["ok"] for c in r.json()["checks"])


async def test_a_dead_dependency_makes_it_unavailable_without_leaking_why():
    r = await get(app_with(db_fail=True))
    assert r.status_code == 503 and r.json()["status"] == "unavailable"
    failed = [c for c in r.json()["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["database"]
    assert failed[0]["error"] == "unavailable"
    assert "password" not in r.text and "10.0.0.4" not in r.text


async def test_a_scheduler_that_is_not_running_is_not_ready():
    r = await get(app_with(scheduler=None))
    assert r.status_code == 503
    assert [c["name"] for c in r.json()["checks"] if not c["ok"]] == ["scheduler"]
