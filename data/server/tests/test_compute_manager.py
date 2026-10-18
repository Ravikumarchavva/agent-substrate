"""The compute controller: attach-only resources and the durable batch-job queue."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from conftest import Served

from loamz.data.server.features.compute.models import JobRequest, ResourceSpec
from loamz.data.server.features.compute.store import ComputeStore

WORKER = "http://127.0.0.1:1"
TOKEN = "w" * 40


def spec(**kwargs) -> ResourceSpec:
    return ResourceSpec(**{"name": "test", "role": "jobs", "url": WORKER, "token": TOKEN, **kwargs})


def chunk_request(text: str = "x", **kwargs) -> JobRequest:
    return JobRequest(operation="chunk", payload={"text": text}, **kwargs)


def test_atomic_claim_and_idempotency(tmp_path):
    store = ComputeStore(tmp_path / "state.sqlite")
    resource = store.create_resource(spec(role="documents"))
    request = chunk_request(idempotency_key="same")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.submit(request, resource["id"]), range(8)))
    assert len({r["id"] for r in results}) == 1
    job_id = results[0]["id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(lambda _: store.claim(job_id, "one"), range(8))) == 1
    with pytest.raises(ValueError, match="different request"):
        store.submit(chunk_request("other", idempotency_key="same"), resource["id"])


def test_stale_attempt_cannot_overwrite_retry_result(tmp_path):
    store = ComputeStore(tmp_path / "state.sqlite")
    resource = store.create_resource(spec(role="documents"))
    job = store.submit(chunk_request(max_attempts=2), resource["id"])
    assert store.claim(job["id"], "first")
    store.finish(job["id"], "failed", retry=True, expected_attempt=1)
    with store.connection() as db:
        db.execute("UPDATE jobs SET available=0 WHERE id=?", (job["id"],))
    assert store.claim(job["id"], "second")
    store.finish(job["id"], "failed", error="late failure from attempt 1", expected_attempt=1)
    assert store.job(job["id"])["state"] == "running"
    store.finish(job["id"], "succeeded", result={"value": 2}, expected_attempt=2)
    assert store.job(job["id"])["result"] == {"value": 2}


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://127.0.0.1", "http://compute.example.com", "http://u:p@127.0.0.1"],
)
def test_attach_url_security(url):
    with pytest.raises(ValueError):
        spec(url=url)
    assert spec(url="https://compute.example.com/").url == "https://compute.example.com"


# ------------------------------------------------------------------ against the running controller


@pytest.fixture(scope="module")
def internal(stack):
    with httpx.Client(
        base_url=stack.controller + "/internal/v1",
        headers={"Authorization": "Bearer " + stack.settings.controller_token.get_secret_value()},
        timeout=30,
    ) as client:
        yield client


def state(internal, resource_id, expected, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = next(
            r for r in internal.get("/resources").json()["resources"] if r["id"] == resource_id
        )
        if value["state"] == expected:
            return value
        time.sleep(0.1)
    raise AssertionError(f"{expected} not reached: {value}")


def wait_job(internal, job_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = internal.get(f"/jobs/{job_id}").json()
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job did not finish: {job}")


@pytest.mark.integration
def test_controller_requires_its_secret(stack):
    assert httpx.get(stack.controller + "/internal/v1/resources").status_code == 401
    assert httpx.get(stack.controller + "/livez").status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize(
    "field", ["provider", "cpus", "memory_mb", "gpus", "min_instances", "mode"]
)
def test_resources_cannot_request_provisioning(internal, field):
    body = {"name": f"p-{field}", "role": "sql", "url": WORKER, "token": TOKEN, field: 1}
    assert internal.post("/resources", json=body).status_code == 422


@pytest.mark.integration
def test_secrets_are_never_returned(stack, internal):
    secret = stack.worker_settings.worker_token.get_secret_value()
    assert secret not in internal.get("/resources").text
    for resource_id in stack.resources.values():
        assert secret not in internal.get(f"/resources/{resource_id}/events").text


@pytest.mark.integration
def test_unhealthy_endpoints_are_reported_with_a_reason(stack, internal, tmp_path):
    from loamz.data.server.features.compute.worker import create_compute_app

    def attach(name, url, token=None):
        body = {
            "name": name,
            "role": "documents",
            "url": url,
            "token": token or stack.worker_settings.worker_token.get_secret_value(),
        }
        return internal.post("/resources", json=body).json()["id"]

    dead = attach("dead", "http://127.0.0.1:9")
    assert "unreachable" in state(internal, dead, "error")["last_error"]
    wrong = attach("wrong-token", stack.workers["documents"].url, "x" * 40)
    assert "token" in state(internal, wrong, "error")["last_error"]
    elsewhere = stack.worker_settings.model_copy(update={"warehouse": "other-warehouse"})
    foreign = Served(create_compute_app(elsewhere, "documents"))
    try:
        other = attach("foreign", foreign.url)
        assert "storage" in state(internal, other, "error")["last_error"]
    finally:
        foreign.stop()
    queued = internal.post(
        "/jobs",
        json={
            "operation": "chunk",
            "payload": {"text": "hello"},
            "resource_id": dead,
            "queue_timeout_seconds": 1,
        },
    ).json()
    assert wait_job(internal, queued["id"])["error"] == "Queue deadline exceeded"
    for resource_id in (dead, wrong, other):
        internal.post(f"/resources/{resource_id}:stop")
        assert internal.post(f"/resources/{resource_id}:delete").status_code == 200


@pytest.mark.integration
def test_jobs_run_and_pausing_leaves_compute_running(stack, internal):
    documents = stack.resources["documents"]
    job = internal.post(
        "/jobs",
        json={"operation": "chunk", "payload": {"text": "a" * 2500}, "resource_id": documents},
    )
    assert job.status_code == 202
    done = wait_job(internal, job.json()["id"])
    assert done["state"] == "succeeded" and len(done["result"]["chunks"]) == 4
    assert internal.post(f"/resources/{documents}:stop").json()["state"] == "stopped"
    # The worker is operated elsewhere: pausing only stops routing to it.
    assert httpx.get(stack.workers["documents"].url + "/livez").status_code == 200
    refused = internal.post(
        "/jobs", json={"operation": "chunk", "payload": {"text": "x"}, "resource_id": documents}
    )
    assert refused.status_code == 400
    internal.post(f"/resources/{documents}:start")
    state(internal, documents, "ready")
    assert internal.post(f"/resources/{documents}:delete").status_code == 400  # must pause first


@pytest.mark.integration
def test_wrong_role_and_bad_payloads_are_rejected(stack, internal):
    sql = stack.resources["sql"]
    assert (
        internal.post(
            "/jobs", json={"operation": "chunk", "payload": {"text": "x"}, "resource_id": sql}
        ).status_code
        == 400
    )
    bad = {
        "operation": "ingest",
        "payload": {
            "sources": ["s3://elsewhere/x"],
            "format": "csv",
            "location": "s3://warehouse/t",
            "write_id": uuid4().hex,
        },
    }
    assert internal.post("/jobs", json=bad).status_code == 400
