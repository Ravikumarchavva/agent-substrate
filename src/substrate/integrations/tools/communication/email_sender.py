"""EmailSenderTool — send emails via configurable SMTP.

Marked as CRITICAL risk with BLOCKING HITL mode — every send requires
explicit human approval since it acts on behalf of the user.
"""

from __future__ import annotations

import re

from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel import TextBlock
from substrate.logger import setup_logging

logger = setup_logging()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailSenderTool:
    """Send emails via SMTP — requires human approval for every send."""

    name = "email_sender"
    description = (
        "Send an email to a recipient via SMTP. Requires human approval before sending."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Email body text.",
            },
            "html": {
                "type": "boolean",
                "description": "Set to true to send body as HTML (default false).",
            },
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False,
    }

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

    async def execute(  # type: ignore[override]
        self,
        *,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        **_: object,
    ) -> ToolExecutionResult:
        if not _EMAIL_RE.match(to):
            return ToolExecutionResult(
                content=[TextBlock(text=f"Invalid email address: {to!r}")],
                is_error=True,
            )

        if not self._smtp_host:
            return ToolExecutionResult(
                content=[TextBlock(text="Email sender not configured (no SMTP host).")],
                is_error=True,
            )

        import aiosmtplib  # type: ignore[import-untyped]
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["From"] = self._from_address or self._smtp_user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html" if html else "plain", "utf-8"))

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
            return ToolExecutionResult(
                content=[TextBlock(text=f"Failed to send email: {exc}")],
                is_error=True,
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f'Email sent to {to}: "{subject}"')],
            structured_content={"to": to, "subject": subject},
        )
