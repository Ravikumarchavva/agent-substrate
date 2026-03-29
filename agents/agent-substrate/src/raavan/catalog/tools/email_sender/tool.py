"""EmailSenderTool — send emails via configurable SMTP.

Marked as CRITICAL risk with BLOCKING HITL mode — every send requires
explicit human approval since it acts on behalf of the user.
"""

from __future__ import annotations

import logging
import re

from raavan.core.tools.base_tool import (
    BaseTool,
    HitlMode,
    ToolResult,
    ToolRisk,
)

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailSenderTool(BaseTool):
    """Send emails via SMTP — requires human approval for every send."""

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_address: str = "",
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._from_address = from_address
        super().__init__(
            name="email_sender",
            description=(
                "Send an email to a recipient. Requires human approval before "
                "every send.  Supports plain-text and HTML body."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body (plain text or HTML)",
                    },
                    "html": {
                        "type": "boolean",
                        "description": "Whether body is HTML (default: false)",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
            risk=ToolRisk.CRITICAL,
            hitl_mode=HitlMode.BLOCKING,
            category="communication",
            tags=["email", "send", "message", "notify", "smtp", "mail"],
            aliases=["send_email", "mail"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
    ) -> ToolResult:
        # Validate email format
        if not _EMAIL_RE.match(to):
            return ToolResult(
                content=[{"type": "text", "text": f"Invalid email address: {to!r}"}],
                is_error=True,
            )

        if not self._smtp_host:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Email sender not configured (no SMTP host).",
                    }
                ],
                is_error=True,
            )

        import aiosmtplib  # type: ignore[import-untyped]
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["From"] = self._from_address or self._smtp_user
        msg["To"] = to
        msg["Subject"] = subject

        content_type = "html" if html else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._smtp_user or None,
                password=self._smtp_password or None,
                start_tls=True,
            )
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
            return ToolResult(
                content=[{"type": "text", "text": f"Failed to send email: {exc}"}],
                is_error=True,
            )

        return ToolResult(
            content=[{"type": "text", "text": f'Email sent to {to}: "{subject}"'}],
            app_data={"to": to, "subject": subject},
        )
