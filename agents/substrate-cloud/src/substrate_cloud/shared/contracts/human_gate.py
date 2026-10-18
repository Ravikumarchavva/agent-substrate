"""The answer to a pending approval or question (``POST /chat/respond/{request_id}``)."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel


class HITLResponse(BaseModel):
    # For tool approval (approve / deny / modify) or human-input signal (answered / skipped / cancelled)
    action: Optional[
        Literal["approve", "deny", "modify", "answered", "skipped", "cancelled"]
    ] = None
    modified_arguments: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    # For human input
    selected_key: Optional[str] = None
    selected_label: Optional[str] = None
    freeform_text: Optional[str] = None
