from __future__ import annotations

from typing import Any, Callable

from raavan.integrations.mcp import app_tools
from raavan.integrations.mcp.app_tools import GoogleWorkspaceTool


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


async def test_google_workspace_tool_requires_connection(monkeypatch) -> None:
    monkeypatch.setattr(app_tools, "get_workspace_access_token", lambda: None)

    result = await GoogleWorkspaceTool().execute(service="calendar")

    assert result.app_data == {
        "service": "calendar",
        "query": "",
        "connected": False,
    }
    assert "not connected" in result.content[0]["text"].lower()


async def test_google_workspace_tool_returns_calendar_summary(monkeypatch) -> None:
    monkeypatch.setattr(app_tools, "get_workspace_access_token", lambda: "token")

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

    result = await GoogleWorkspaceTool().execute(service="calendar")
    text = result.content[0]["text"]

    assert "Upcoming calendar events" in text
    assert "Team Sync" in text
    assert "Zoom" in text
    assert result.app_data == {
        "service": "calendar",
        "query": "",
        "connected": True,
    }


async def test_google_workspace_tool_returns_gmail_summary(monkeypatch) -> None:
    monkeypatch.setattr(app_tools, "get_workspace_access_token", lambda: "token")

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

    result = await GoogleWorkspaceTool().execute(service="gmail")
    text = result.content[0]["text"]

    assert "Recent inbox messages" in text
    assert "Alice Example" in text
    assert "Project update" in text
    assert result.app_data == {
        "service": "gmail",
        "query": "",
        "connected": True,
    }
