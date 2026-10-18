"""A batch job that outgrows its memory budget is stopped; it never takes the machine down."""

from __future__ import annotations

import time

import hog
import pytest
from pydantic import SecretStr

from loamz.data.server.features.compute import worker as service
from loamz.data.server.features.compute.worker import JobRunner, memory_ceiling, rss_bytes
from loamz.data.server.kernel.settings import Settings


def settings(**fields) -> Settings:
    return Settings(_env_file=None, worker_token=SecretStr("w" * 40), **fields)


def finished(runner: JobRunner, execution_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = runner.public(execution_id)
        if state["state"] != "running":
            return state
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_the_ceiling_is_configurable_and_defaults_to_half_the_memory_available():
    assert memory_ceiling(settings(worker_job_memory_mb=512)) == 512 * 2**20
    assert memory_ceiling(settings()) == service.available_memory() // 2


def test_resident_memory_of_a_process_can_be_read():
    import os

    assert rss_bytes(os.getpid()) > 10 * 2**20
    assert rss_bytes(2**22 + 12345) == 0  # no such process


def test_a_runaway_job_is_stopped_with_a_clear_reason(monkeypatch):
    monkeypatch.setattr(service, "run_job", hog.runaway)
    runner = JobRunner(settings(worker_job_memory_mb=400))
    runner.start("a" * 32, "optimize", {}, 60)
    outcome = finished(runner, "a" * 32)
    assert outcome["state"] == "failed"
    assert "memory" in outcome["error"] and "smaller slice" in outcome["error"]
    # The slot is free again: the worker keeps serving other jobs.
    monkeypatch.setattr(service, "run_job", hog.polite)
    runner.start("b" * 32, "optimize", {}, 60)
    assert finished(runner, "b" * 32)["state"] == "succeeded"


def test_a_job_within_budget_is_left_alone(monkeypatch):
    monkeypatch.setattr(service, "run_job", hog.polite)
    runner = JobRunner(settings(worker_job_memory_mb=2048))
    runner.start("c" * 32, "optimize", {}, 60)
    outcome = finished(runner, "c" * 32)
    assert outcome["state"] == "succeeded" and outcome["result"] == {"bytes": 20 * 2**20}


@pytest.mark.integration
def test_a_sort_too_big_for_one_worker_is_refused_up_front(stack, tmp_path):
    import polars as pl
    from conftest import ADMIN

    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "limits")
    path = tmp_path / "t.parquet"
    pl.DataFrame({"k": range(1000), "v": ["x"] * 1000}).write_parquet(path)
    admin.upload("main.limits.big", [path])
    services = stack.app.state.container
    original = services.settings.sort_max_input_mb
    services.settings.sort_max_input_mb = 0
    try:
        with stack.http("alice", *ADMIN) as http:
            reply = http.post(
                "/catalogs/main/schemas/limits/tables/big:optimize",
                json={"strategy": "sort", "sort_by": ["k"]},
            )
            assert reply.status_code == 413
            assert reply.json()["type"].endswith("quota-exceeded")
            assert "where" in reply.json()["detail"]
            # Compaction does not hold the data in memory, so it is not subject to the sort limit.
            assert (
                http.post(
                    "/catalogs/main/schemas/limits/tables/big:optimize",
                    json={"strategy": "compact"},
                ).status_code
                == 202
            )
    finally:
        services.settings.sort_max_input_mb = original
