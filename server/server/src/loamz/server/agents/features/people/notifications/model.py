from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 2000


class Notification(BaseModel):
    """Something the person should know about: a scheduled run finished or failed, or the assistant is waiting."""

    id: str
    kind: str
    title: str
    body: str = ""
    thread_id: str | None = None
    link: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class NotificationPage(BaseModel):
    items: list[Notification]
    next_page_token: str | None = None
    unread: int = 0
    """How many of the person's notifications are unread (all of them, not just this page)."""
