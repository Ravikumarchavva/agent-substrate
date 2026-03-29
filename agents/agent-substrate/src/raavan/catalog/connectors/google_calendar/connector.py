"""GoogleCalendarConnector — read/write Google Calendar events.

Uses Google Calendar API via httpx with OAuth2 service account or
user-delegated credentials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GoogleCalendarConnector:
    """Async Google Calendar API connector.

    Parameters
    ----------
    credentials_json
        Path to service-account JSON key file.
    calendar_id
        Default calendar ID (usually an email address).
    """

    def __init__(
        self,
        credentials_json: str = "",
        calendar_id: str = "primary",
    ) -> None:
        self._credentials_json = credentials_json
        self._calendar_id = calendar_id
        self._client: Optional[Any] = None

    async def connect(self) -> None:
        """Authenticate and create HTTP client."""
        import httpx

        self._client = httpx.AsyncClient(
            base_url="https://www.googleapis.com/calendar/v3",
            timeout=30.0,
        )

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_events(
        self,
        *,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10,
        calendar_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List upcoming calendar events."""
        assert self._client is not None, "Not connected"
        cal = calendar_id or self._calendar_id
        params: Dict[str, Any] = {
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        else:
            params["timeMin"] = datetime.now(timezone.utc).isoformat()
        if time_max:
            params["timeMax"] = time_max

        resp = await self._client.get(f"/calendars/{cal}/events", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def create_event(
        self,
        *,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        attendees: Optional[List[str]] = None,
        calendar_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new calendar event."""
        assert self._client is not None, "Not connected"
        cal = calendar_id or self._calendar_id
        body: Dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if description:
            body["description"] = description
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]

        resp = await self._client.post(f"/calendars/{cal}/events", json=body)
        resp.raise_for_status()
        return resp.json()

    async def check_availability(
        self,
        *,
        time_min: str,
        time_max: str,
        calendar_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Check free/busy for a time range."""
        assert self._client is not None, "Not connected"
        cal = calendar_id or self._calendar_id
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": cal}],
        }
        resp = await self._client.post("/freeBusy", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data.get("calendars", {}).get(cal, {}).get("busy", [])
