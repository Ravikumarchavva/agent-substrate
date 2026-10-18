"""Crash recovery across real processes (opt-in): publications and job leases survive restarts."""

from __future__ import annotations

import time

import httpx
import polars as pl
import pytest

pytestmark = pytest.mark.integration


def big_csv(path, rows=2_000_000):
    pl.DataFrame(
        {
            "id": range(rows),
            "group": [i % 100 for i in range(rows)],
            "value": [i * 0.5 for i in range(rows)],
        }
    ).write_csv(path)
    return path


def test_api_crash_after_queueing_still_publishes(process_stack, tmp_path):
    client = process_stack.client()
    client.create_schema("main", "crash")
    record = client.upload("main.crash.api", [big_csv(tmp_path / "a.csv")], wait=False)
    process_stack.kill("api")  # mid-import: the infer/ingest jobs keep running on compute
    process_stack.start("api")
    deadline = time.monotonic() + 180
    while record["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
        time.sleep(1)
        record = client.request("GET", f"/imports/{record['id']}")
    assert record["status"] == "succeeded", record
    assert client.sql("SELECT count(*) FROM crash.api").item() == 2_000_000


def test_controller_crash_replays_the_job_once(process_stack, tmp_path):
    client = process_stack.client()
    client.create_schema("main", "crash2")
    record = client.upload("main.crash2.ctl", [big_csv(tmp_path / "b.csv")], wait=False)
    internal = httpx.Client(
        base_url=process_stack.env["SUBSTRATE_DATA_CONTROLLER_URL"] + "/internal/v1",
        headers={"Authorization": "Bearer " + process_stack.env["SUBSTRATE_DATA_CONTROLLER_TOKEN"]},
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(
            j["state"] == "running" and j["operation"] == "ingest"
            for j in internal.get("/jobs").json()["jobs"]
        ):
            break
        time.sleep(0.2)
    process_stack.kill("controller")
    process_stack.start("controller")
    deadline = time.monotonic() + 240
    while record["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
        time.sleep(1)
        record = client.request("GET", f"/imports/{record['id']}")
    assert record["status"] == "succeeded", record
    # The interrupted attempt's files are never published: exactly one copy of the data.
    assert client.sql("SELECT count(*) FROM crash2.ctl").item() == 2_000_000
    internal.close()
