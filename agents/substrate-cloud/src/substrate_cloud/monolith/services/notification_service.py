"""Telling the user about things that happened while they were away: a scheduled run finished or failed, or the assistant is waiting on them.

A notification is a row (shown in the app's notification centre) and, for a scheduled task the user asked to be emailed about, an email.
Both are best effort: a failed email never fails the run that caused it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import Notification

logger = logging.getLogger(__name__)

EMAIL_TIMEOUT_S = 10.0


async def notify(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_identifier: str,
    kind: str,
    title: str,
    body: str = "",
    thread_id: Optional[uuid.UUID] = None,
) -> Notification:
    """Record a notification for one user. The caller commits."""
    row = Notification(
        tenant_id=tenant_id,
        user_identifier=user_identifier,
        kind=kind,
        title=title[:200],
        body=body[:2000],
        thread_id=thread_id,
    )
    db.add(row)
    await db.flush()
    return row


async def send_email(
    *,
    to: str,
    subject: str,
    text: str,
    api_key: str,
    sender: str,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Send one plain-text email through Resend. ``False`` (never an exception) when there is no key, no address or the send fails."""
    if not api_key or not to:
        return False
    try:
        async with client or httpx.AsyncClient(timeout=EMAIL_TIMEOUT_S) as http:
            response = await http.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": sender,
                    "to": [to],
                    "subject": subject[:200],
                    "text": text,
                },
            )
        if response.status_code >= 300:
            logger.warning("email to %s was refused: HTTP %s", to, response.status_code)
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — notification is best effort
        logger.warning("email to %s failed: %s", to, exc)
        return False
