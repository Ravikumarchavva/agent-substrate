"""Shared API contracts for the workflow orchestrator."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowRunCreate(BaseModel):
    """Command to start a new agent workflow run."""

    thread_id: uuid.UUID
    user_content: str
    system_instructions: Optional[str] = None
    file_ids: Optional[List[uuid.UUID]] = None
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "default"
    workspace_id: str = "default"


class WorkflowRunOut(BaseModel):
    """Workflow run state."""

    id: uuid.UUID
    thread_id: uuid.UUID
    status: str  # pending, running, completed, failed, cancelled
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    model_config = {"from_attributes": True}


class WorkflowCancelRequest(BaseModel):
    """Command to cancel a running workflow."""

    reason: str = "user_cancelled"


class WorkflowRetryRequest(BaseModel):
    """Command to retry a failed workflow run."""

    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
