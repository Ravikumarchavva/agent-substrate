from __future__ import annotations

import json
from typing import Any, Callable

from ravi.integrations.mcp import app_tools
from ravi.integrations.mcp.app_tools import GoogleWorkspaceTool


class _FakeRedis:
    """Minimal async Redis stub that stores a single key."""

    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = data or {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""
        self.reason_phrase = "OK"

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, handler: Callable[[str, Any], _FakeResponse]) -> None:
        self._handler = handler

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, params: Any = None) -> _FakeResponse:
        return self._handler(url, params)

    async def post(self, url: str, json: Any = None) -> _FakeResponse:
        return self._handler(url, json)

    async def delete(self, url: str) -> _FakeResponse:
        return self._handler(url, None)


def _tool_disconnected() -> GoogleWorkspaceTool:
    return GoogleWorkspaceTool(redis_client=_FakeRedis())


def _tool_connected() -> GoogleWorkspaceTool:
    redis = _FakeRedis(
        {
            "workspace_token:default_user": json.dumps(
                {
                    "access_token": "token",
                    "refresh_token": None,
                    "expires_in": 3600,
                    "expires_at": 4_102_444_800,
                }
            )
        }
    )
    return GoogleWorkspaceTool(redis_client=redis)


async def test_google_workspace_tool_requires_connection() -> None:
    result = await _tool_disconnected().execute(service="calendar")

    assert result.app_data == {
        "service": "calendar",
        "query": "",
        "connected": False,
    }
    assert "not connected" in result.content[0].text.lower()


async def test_google_workspace_tool_returns_calendar_summary(monkeypatch) -> None:
    def handler(url: str, params: Any) -> _FakeResponse:
        assert "calendar/v3/calendars/primary/events" in url
        return _FakeResponse(
            {
                "items": [
                    {
                        "summary": "Team Sync",
                        "location": "Zoom",
                        "start": {"dateTime": "2026-04-19T10:00:00Z"},
                    }
                ]
            }
        )

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await _tool_connected().execute(service="calendar")
    text = result.content[0].text

    assert "Upcoming calendar events" in text
    assert "Team Sync" in text
    assert "Zoom" in text
    assert result.app_data == {
        "service": "calendar",
        "query": "",
        "connected": True,
    }


async def test_google_workspace_tool_returns_gmail_summary(monkeypatch) -> None:
    def handler(url: str, params: Any) -> _FakeResponse:
        if url.endswith("/gmail/v1/users/me/messages"):
            return _FakeResponse({"messages": [{"id": "msg-1"}]})

        assert "/gmail/v1/users/me/messages/msg-1" in url
        assert params == [
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "Subject"),
            ("metadataHeaders", "Date"),
        ]
        return _FakeResponse(
            {
                "id": "msg-1",
                "snippet": "Please review the notes.",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Alice Example <alice@example.com>"},
                        {"name": "Subject", "value": "Project update"},
                        {"name": "Date", "value": "Fri, 18 Apr 2026 10:00:00 +0000"},
                    ]
                },
            }
        )

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await _tool_connected().execute(service="gmail")
    text = result.content[0].text

    assert "Recent inbox messages" in text
    assert "Alice Example" in text
    assert "Project update" in text
    assert result.app_data == {
        "service": "gmail",
        "query": "",
        "connected": True,
    }


async def test_google_workspace_tool_ignores_textual_fallback_action() -> None:
    result = await _tool_disconnected().execute(
        service="gmail",
        query="",
        action="summarize_mails",
    )

    assert result.app_data == {
        "service": "gmail",
        "query": "",
        "connected": False,
    }
    assert "not connected" in result.content[0].text.lower()


async def test_google_workspace_tool_clears_stale_token_after_google_401(
    monkeypatch,
) -> None:
    redis = _FakeRedis(
        {
            "workspace_token:default_user": json.dumps(
                {
                    "access_token": "token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                    "expires_at": 4_102_444_800,
                }
            )
        }
    )

    def handler(url: str, params: Any) -> _FakeResponse:
        return _FakeResponse(
            {
                "error": {
                    "message": "Request had invalid authentication credentials.",
                }
            },
            status_code=401,
        )

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await GoogleWorkspaceTool(redis_client=redis).execute(service="gmail")

    assert result.app_data == {
        "service": "gmail",
        "query": "",
        "connected": False,
    }
    assert "refresh the token" in result.content[0].text.lower()
    assert await redis.get("workspace_token:default_user") is None


async def test_google_workspace_tool_create_event(monkeypatch) -> None:
    def handler(url: str, body: Any) -> _FakeResponse:
        assert "calendar/v3/calendars/primary/events" in url
        assert body["summary"] == "IPL Final"
        return _FakeResponse(
            {
                "id": "event-123",
                "htmlLink": "https://calendar.google.com/event?eid=event-123",
                "summary": "IPL Final",
            }
        )

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await _tool_connected().execute(
        service="calendar",
        action="create_event",
        title="IPL Final",
        start_time="2026-04-20T19:00:00+05:30",
        end_time="2026-04-20T22:00:00+05:30",
    )

    assert not result.is_error
    text = result.content[0].text
    assert "Created event" in text
    assert "IPL Final" in text
    assert "Event ID: event-123" in text
    assert result.app_data == {
        "service": "calendar",
        "query": "",
        "connected": True,
        "calendar_mutated": True,
        "action": "create_event",
    }


async def test_google_workspace_tool_create_event_defaults_end_time(
    monkeypatch,
) -> None:
    """When end_time is omitted, it should default to 1 hour after start."""
    received: list[dict] = []

    def handler(url: str, body: Any) -> _FakeResponse:
        received.append(body)
        return _FakeResponse({"id": "event-456", "summary": "Stand-up"})

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await _tool_connected().execute(
        service="calendar",
        action="create_event",
        title="Stand-up",
        start_time="2026-04-21T09:00:00+05:30",
    )

    assert not result.is_error
    body = received[0]
    # end should be 10:00 IST = 1 hour after 09:00
    assert "10:00:00" in body["end"]["dateTime"]


async def test_google_workspace_tool_create_event_missing_title() -> None:
    result = await _tool_connected().execute(
        service="calendar",
        action="create_event",
        start_time="2026-04-20T19:00:00+05:30",
    )

    assert result.is_error
    assert "title is required" in result.content[0].text


async def test_google_workspace_tool_cancel_event(monkeypatch) -> None:
    def handler(url: str, body: Any) -> _FakeResponse:
        assert "events/event-abc" in url
        return _FakeResponse({}, status_code=204)

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await _tool_connected().execute(
        action="cancel_event",
        event_id="event-abc",
    )

    assert not result.is_error
    assert "cancelled" in result.content[0].text.lower()
    assert result.app_data == {
        "service": "calendar",
        "query": "",
        "connected": True,
        "calendar_mutated": True,
        "action": "cancel_event",
    }


async def test_google_workspace_tool_cancel_event_not_found(monkeypatch) -> None:
    def handler(url: str, body: Any) -> _FakeResponse:
        return _FakeResponse({}, status_code=404)

    monkeypatch.setattr(
        app_tools.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(handler),
    )

    result = await _tool_connected().execute(
        action="cancel_event",
        event_id="nonexistent",
    )

    assert result.is_error
    assert "not found" in result.content[0].text.lower()


async def test_google_workspace_tool_cancel_event_missing_id() -> None:
    result = await _tool_connected().execute(
        action="cancel_event",
    )

    assert result.is_error
    assert "event_id is required" in result.content[0].text
