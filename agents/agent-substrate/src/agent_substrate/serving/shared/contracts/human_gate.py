"""Shared API contracts for HITL approval service."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel


class HITLApprovalRequest(BaseModel):
    """An approval request emitted when a critical tool is about to execute."""

    request_id: str
    run_id: str
    thread_id: str
    tool_name: str
    arguments: Dict[str, Any]
    risk_tier: str = "critical"
    timeout_seconds: float = 300.0


class HITLInputRequest(BaseModel):
    """A human input request emitted when the agent needs user input."""

    request_id: str
    run_id: str
    thread_id: str
    prompt: str
    options: Optional[list[Dict[str, str]]] = None


class HITLResponse(BaseModel):
    """User response to an HITL request."""

    # For tool approval
    action: Optional[Literal["approve", "deny", "modify"]] = None
    modified_arguments: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    # For human input
    selected_key: Optional[str] = None
    selected_label: Optional[str] = None
    freeform_text: Optional[str] = None


class HITLStatusOut(BaseModel):
    """Pending HITL requests for a thread."""

    pending: list[Dict[str, Any]]
