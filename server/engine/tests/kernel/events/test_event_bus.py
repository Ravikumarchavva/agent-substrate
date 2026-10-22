"""The in-memory bus held to the EventBus suite, and the typed catalog."""

from __future__ import annotations

import pytest

from loamz.engine.agents.kernel.events import (
    AUDITED,
    CATALOG,
    CancelRequested,
    EventEnvelope,
    InMemoryEventBus,
    ThreadCreated,
    parse_event,
)
from loamz.engine.agents.testing.conformance.event_bus import EventBusConformance
from loamz.engine.errors import InvalidRequestError


class TestInMemoryEventBus(EventBusConformance):
    @pytest.fixture
    async def bus(self):
        return InMemoryEventBus()


def test_every_catalog_event_names_its_tenant_and_user() -> None:
    for event_type, cls in CATALOG.items():
        assert cls.event_type == event_type
        assert {"tenant_id", "user_id"} <= set(cls.model_fields)


def test_cancel_requests_are_not_audited_and_the_rest_are() -> None:
    assert CancelRequested.event_type not in AUDITED
    assert set(AUDITED) == set(CATALOG) - {CancelRequested.event_type}


def test_an_envelope_parses_back_to_its_typed_event() -> None:
    event = ThreadCreated(thread_id="t1", name="Plans", tenant_id="acme", user_id="ann")
    envelope = event.envelope()
    assert envelope.event_type == "conversation.thread_created"
    assert parse_event(envelope) == event


def test_an_unknown_event_type_is_refused() -> None:
    with pytest.raises(InvalidRequestError):
        parse_event(EventEnvelope(event_type="nobody.knows", payload={}))
