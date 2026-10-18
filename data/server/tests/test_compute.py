"""Worker security: only signed tickets run, and even a signed statement cannot escape storage."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from conftest import ADMIN
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from loamz.data.server.features.compute.manifests import storage_identity
from loamz.data.server.kernel.tickets import TicketSigner

pytestmark = pytest.mark.integration


def ticket(signer: TicketSigner, sql: str, tables: list | None = None) -> str:
    token, _ = signer.sign(
        {
            "op": "query",
            "sub": "user:test",
            "sql": sql,
            "tables": tables or [],
            "limits": {"max_rows": 10, "max_bytes": 10_000_000, "timeout_ms": 5000},
        }
    )
    return token


@pytest.fixture(scope="module")
def signer(ticket_keys):
    return TicketSigner(ticket_keys[0], 60)


@pytest.fixture(scope="module")
def sql_worker(stack):
    return stack.workers["sql"].url + "/internal/v1/statements"


def test_statements_need_a_valid_ticket(stack, signer, sql_worker, tmp_path):
    assert httpx.post(sql_worker).status_code == 401
    access_token = stack.token("alice", *ADMIN)
    assert (
        httpx.post(sql_worker, headers={"Authorization": "Ticket " + access_token}).status_code
        == 401
    )
    assert (
        httpx.post(sql_worker, headers={"Authorization": "Bearer " + access_token}).status_code
        == 401
    )
    other = Ed25519PrivateKey.generate()
    path = tmp_path / "other.pem"
    path.write_bytes(
        other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    forged = ticket(TicketSigner(path, 60), "SELECT 1")
    assert httpx.post(sql_worker, headers={"Authorization": "Ticket " + forged}).status_code == 401
    good = ticket(signer, "SELECT 42 AS answer")
    first = httpx.post(sql_worker, headers={"Authorization": "Ticket " + good})
    assert first.status_code == 200 and first.headers["content-type"].startswith(
        "application/vnd.apache.arrow.stream"
    )
    replay = httpx.post(sql_worker, headers={"Authorization": "Ticket " + good})
    assert replay.status_code == 401 and "already used" in replay.json()["detail"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('s3://loamz-staging/anything.parquet')",
        "SELECT * FROM read_parquet('http://example.com/x.parquet')",
        "COPY (SELECT 1) TO '/tmp/escape.csv'",
        "SET enable_external_access = true",
        "INSTALL spatial",
        "ATTACH '/tmp/x.db'",
    ],
)
def test_signed_statements_still_cannot_escape_the_warehouse(signer, sql_worker, sql):
    """Defence in depth: the planner never signs these, but a stolen key still gains little."""
    response = httpx.post(sql_worker, headers={"Authorization": "Ticket " + ticket(signer, sql)})
    assert response.status_code == 400, response.text


def test_job_api_needs_the_controller_secret_and_stays_in_the_warehouse(stack):
    jobs = stack.workers["jobs"].url + "/internal/v1/jobs"
    envelope = {
        "execution_id": uuid4().hex,
        "operation": "ingest",
        "storage_id": storage_identity(stack.worker_settings),
        "timeout_seconds": 60,
        "payload": {
            "sources": ["s3://loamz-staging/x.csv"],
            "format": "csv",
            "location": "s3://other-bucket/table",
            "write_id": uuid4().hex,
        },
    }
    assert httpx.post(jobs, json=envelope).status_code == 401
    secret = {"Authorization": "Bearer " + stack.worker_settings.worker_token.get_secret_value()}
    outside = httpx.post(jobs, json=envelope, headers=secret)
    assert outside.status_code == 400 and "warehouse" in outside.json()["detail"]
    envelope["payload"]["location"] = "s3://warehouse/main/x"
    envelope["payload"]["sources"] = ["s3://warehouse/main/other/data/f.parquet"]
    assert httpx.post(jobs, json=envelope, headers=secret).status_code == 400
    envelope["storage_id"] = "elsewhere"
    assert httpx.post(jobs, json=envelope, headers=secret).status_code == 409


def test_sql_worker_runs_no_batch_jobs_and_workers_need_no_catalog(stack):
    response = httpx.post(stack.workers["sql"].url + "/internal/v1/jobs", json={})
    assert response.status_code == 404
    assert stack.worker_settings.ticket_private_key_file is None
    assert stack.worker_settings.controller_token.get_secret_value() == ""
