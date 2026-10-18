"""The services' domain events: each one has a producer and a consumer, and none is left that nobody uses."""

from __future__ import annotations

import re
from pathlib import Path

from substrate_cloud.shared.events import types as events

SERVICES = Path(__file__).parent.parent / "src" / "substrate_cloud" / "services"


def service_sources() -> str:
    return "\n".join(p.read_text() for p in SERVICES.rglob("*.py"))


def test_every_event_has_a_producer_and_a_consumer():
    """The pruned event set is exactly the events something publishes and something consumes."""
    sources = service_sources()
    constants = {
        name: value
        for name, value in vars(events).items()
        if name.isupper() and isinstance(value, str) and "." in value
    }
    assert set(constants.values()) == {
        "conversation.thread_created",
        "conversation.thread_deleted",
        "workflow.run_started",
        "workflow.run_completed",
        "workflow.run_failed",
        "workflow.run_cancelled",
        "job.cancel_requested",
    }
    producers = {
        events.THREAD_CREATED: "events.thread_created(",
        events.THREAD_DELETED: "events.thread_deleted(",
        events.RUN_STARTED: "events.workflow_started(",
        events.RUN_COMPLETED: "events.workflow_completed(",
        events.RUN_FAILED: "events.workflow_failed(",
        events.RUN_CANCELLED: "events.workflow_cancelled(",
        events.CANCEL_REQUESTED: "events.job_cancel_requested(",
    }
    for event_type, call in producers.items():
        assert call in sources, f"{event_type} is never published"
    consumed = set(re.findall(r"(?:follow|subscribe)\(\s*events\.([A-Z_]+)", sources))
    if (
        "for t in events.AUDITED" in sources
    ):  # the admin service audits every event in AUDITED
        consumed |= {n for n, v in constants.items() if v in events.AUDITED}
    for name, value in constants.items():
        assert name in consumed, f"{value} is never consumed"
