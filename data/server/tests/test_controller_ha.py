"""Several controllers on one PostgreSQL database: one schedules, the rest wait, and one takes over."""

from __future__ import annotations

import secrets
import subprocess
import time

import httpx
import pytest
from conftest import Served, start_postgres
from pydantic import SecretStr

from loamz.data.server.features.compute.control_api import create_control_app
from loamz.data.server.kernel.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def server():
    server = start_postgres()
    yield server
    subprocess.run(["docker", "rm", "-f", server.name], capture_output=True)


class Node:
    def __init__(self, url: str, token: str, tmp_path, name: str):
        settings = Settings(
            _env_file=None,
            controller_token=SecretStr(token),
            controller_state=tmp_path / name,
            controller_state_url=SecretStr(url),
            warehouse="warehouse",
        )
        self.app = create_control_app(settings)
        self.controller = self.app.state.controller
        self.served = Served(self.app)
        self.http = httpx.Client(
            base_url=self.served.url,
            headers={"Authorization": "Bearer " + token},
            timeout=10,
        )

    @property
    def ready(self) -> bool:
        return self.http.get("/readyz").status_code == 200

    def stop(self) -> None:
        self.http.close()
        self.served.stop()


def partition(node: Node) -> None:
    """The node loses its database session for good (a crash, or a network partition): the server
    frees its lock, and the node can neither keep nor retake it."""
    node.controller.election.conn.close()
    node.controller.election.acquire = lambda: False


def wait_for(check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.2)
    raise AssertionError("condition not reached in time")


@pytest.fixture
def pair(server, tmp_path):
    token, url = secrets.token_urlsafe(48), server.database()
    a = Node(url, token, tmp_path, "a")
    wait_for(lambda: a.ready)  # a starts first and wins
    b = Node(url, token, tmp_path, "b")
    yield a, b
    for node in (b, a):
        node.stop()


def test_one_controller_serves_and_the_other_waits(pair):
    a, b = pair
    assert a.ready and not b.ready
    assert a.controller.active and not b.controller.active
    standby = b.http.get("/internal/v1/resources")
    assert standby.status_code == 503 and standby.json()["type"].endswith("not-leader")
    assert standby.headers["retry-after"] == "1"
    assert a.http.get("/internal/v1/resources").status_code == 200
    assert b.http.get("/livez").status_code == 200  # alive, just not in charge


def test_state_is_shared_so_a_takeover_loses_nothing(pair):
    a, b = pair
    created = a.http.post(
        "/internal/v1/resources",
        json={
            "name": "kept",
            "role": "jobs",
            "url": "http://127.0.0.1:9",
            "token": secrets.token_urlsafe(40),
        },
    )
    assert created.status_code == 201, created.text
    resource_id = created.json()["id"]
    job = a.http.post(
        "/internal/v1/jobs",
        json={
            "operation": "delete",
            "resource_id": resource_id,
            "payload": {"paths": ["s3://warehouse/ns/t/data/x/f.parquet"]},
            "idempotency_key": "ha-test-1",
        },
    )
    assert job.status_code in (200, 201, 202), job.text

    # The active controller dies the way a crashed process does: its database connection drops.
    partition(a)
    wait_for(lambda: b.ready)
    wait_for(lambda: not a.controller.active)  # it noticed and stood down
    assert a.http.get("/internal/v1/resources").status_code == 503
    listed = b.http.get("/internal/v1/resources").json()["resources"]
    assert [r["name"] for r in listed] == ["kept"]
    jobs = b.http.get("/internal/v1/jobs").json()
    assert [j["id"] for j in jobs["jobs"]] == [job.json()["id"]]  # the queue survived
    again = b.http.post(
        "/internal/v1/jobs",
        json={
            "operation": "delete",
            "resource_id": resource_id,
            "payload": {"paths": ["s3://warehouse/ns/t/data/x/f.parquet"]},
            "idempotency_key": "ha-test-1",
        },
    )
    assert (
        again.json()["id"] == job.json()["id"]
    )  # idempotency keys are honoured across controllers


def test_a_controller_that_shuts_down_cleanly_hands_over_immediately(pair):
    a, b = pair
    started = time.monotonic()
    a.controller.close()
    wait_for(lambda: b.ready)
    assert time.monotonic() - started < 10


def test_exactly_one_controller_is_active_through_repeated_failovers(server, tmp_path):
    token, url = secrets.token_urlsafe(48), server.database()
    nodes = [Node(url, token, tmp_path, name) for name in ("x", "y", "z")]
    try:
        for _ in range(2):
            leader = wait_for(lambda: next((n for n in nodes if n.ready), None))
            assert sum(1 for n in nodes if n.controller.active) == 1
            nodes.remove(leader)
            partition(leader)
            wait_for(lambda leader=leader: not leader.controller.active)
            leader.stop()
        survivor = wait_for(lambda: next((n for n in nodes if n.ready), None))
        assert sum(1 for n in nodes if n.controller.active) == 1 and survivor.ready
    finally:
        for n in nodes:
            n.stop()


def test_interrupted_work_is_replayed_by_the_new_leader(pair):
    a, b = pair
    created = a.http.post(
        "/internal/v1/resources",
        json={
            "name": "w",
            "role": "jobs",
            "url": "http://127.0.0.1:9",
            "token": secrets.token_urlsafe(40),
        },
    )
    resource_id = created.json()["id"]
    job = a.http.post(
        "/internal/v1/jobs",
        json={
            "operation": "delete",
            "resource_id": resource_id,
            "payload": {"paths": ["s3://warehouse/ns/t/data/x/f.parquet"]},
            "max_attempts": 3,
        },
    ).json()
    # Pretend the old leader had started it when it died.
    assert a.controller.store.claim(job["id"], "old-leader-instance")
    partition(a)
    wait_for(lambda: b.ready)
    recovered = wait_for(
        lambda: (
            (j := b.http.get(f"/internal/v1/jobs/{job['id']}").json())["state"] != "running" and j
        )
    )
    assert "lease revoked" in (recovered.get("error") or "") or recovered["state"] == "queued"
    assert recovered["attempts"] >= 1 and recovered["state"] in {"queued", "failed"}
