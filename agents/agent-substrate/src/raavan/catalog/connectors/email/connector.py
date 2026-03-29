"""EmailConnector — send and receive emails via SMTP and IMAP.

Extends beyond the basic EmailSenderTool by adding IMAP receive/search
capabilities.
"""

from __future__ import annotations

import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailConnector:
    """Async email connector for SMTP send and IMAP receive.

    Parameters
    ----------
    smtp_host / smtp_port
        SMTP server for sending.
    imap_host / imap_port
        IMAP server for receiving.
    username / password
        Credentials for both SMTP and IMAP.
    from_address
        Default sender address.
    """

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        imap_host: str = "",
        imap_port: int = 993,
        username: str = "",
        password: str = "",
        from_address: str = "",
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._username = username
        self._password = password
        self._from_address = from_address or username

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
    ) -> Dict[str, Any]:
        """Send an email via SMTP."""
        if not _EMAIL_RE.match(to):
            raise ValueError(f"Invalid email address: {to!r}")
        if not self._smtp_host:
            raise RuntimeError("SMTP not configured")

        import aiosmtplib  # type: ignore[import-untyped]

        msg = MIMEMultipart("alternative")
        msg["From"] = self._from_address
        msg["To"] = to
        msg["Subject"] = subject

        content_type = "html" if html else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=self._smtp_host,
            port=self._smtp_port,
            username=self._username or None,
            password=self._password or None,
            start_tls=True,
        )
        return {"status": "sent", "to": to, "subject": subject}

    async def receive(
        self,
        *,
        folder: str = "INBOX",
        limit: int = 10,
        search_criteria: str = "ALL",
    ) -> List[Dict[str, Any]]:
        """Fetch recent emails via IMAP."""
        if not self._imap_host:
            raise RuntimeError("IMAP not configured")

        import aioimaplib  # type: ignore[import-untyped]
        import email as email_lib

        client = aioimaplib.IMAP4_SSL(host=self._imap_host, port=self._imap_port)
        await client.wait_hello_from_server()
        await client.login(self._username, self._password)
        await client.select(folder)

        _, data = await client.search(search_criteria)
        ids = data[0].split()
        results: List[Dict[str, Any]] = []

        for msg_id in ids[-limit:]:
            _, msg_data = await client.fetch(msg_id.decode(), "(RFC822)")
            if msg_data and len(msg_data) > 1:
                raw_email = msg_data[1]
                if isinstance(raw_email, tuple) and len(raw_email) > 1:
                    parsed = email_lib.message_from_bytes(raw_email[1])
                    results.append(
                        {
                            "id": msg_id.decode(),
                            "from": parsed.get("From", ""),
                            "to": parsed.get("To", ""),
                            "subject": parsed.get("Subject", ""),
                            "date": parsed.get("Date", ""),
                        }
                    )

        await client.logout()
        return results

    async def search(
        self,
        *,
        query: str,
        folder: str = "INBOX",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search emails by subject or sender."""
        criteria = f'(OR SUBJECT "{query}" FROM "{query}")'
        return await self.receive(folder=folder, limit=limit, search_criteria=criteria)
