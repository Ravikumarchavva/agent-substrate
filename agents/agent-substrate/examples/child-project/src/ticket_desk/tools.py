"""The agent's tools: plain functions, made tools with ``@tool``."""

from __future__ import annotations

from typing import Literal

from substrate import ToolRisk, tool

TICKETS: dict[str, dict[str, str]] = {
    "T-100": {"status": "open", "subject": "Printer is on fire"},
    "T-101": {"status": "open", "subject": "Cannot log in"},
}


@tool(risk=ToolRisk.SAFE, idempotent=True, concurrency_safe=True)
def lookup_ticket(ticket_id: str) -> dict[str, str]:
    """Find a ticket.

    Args:
        ticket_id: The ticket's id, e.g. T-100.
    """
    return TICKETS.get(ticket_id) or {"error": f"no ticket {ticket_id}"}


@tool(risk=ToolRisk.HIGH, idempotent=True)  # a person approves before a ticket is closed; closing twice is harmless
def close_ticket(ticket_id: str, resolution: Literal["fixed", "wont_fix", "duplicate"]) -> str:
    """Close a ticket with a resolution.

    Args:
        ticket_id: The ticket to close.
        resolution: Why it is being closed.
    """
    if ticket_id not in TICKETS:
        return f"no ticket {ticket_id}"
    TICKETS[ticket_id]["status"] = f"closed ({resolution})"
    return f"{ticket_id} closed as {resolution}"
