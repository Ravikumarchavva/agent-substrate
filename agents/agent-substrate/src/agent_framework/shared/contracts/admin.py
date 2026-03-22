"""Shared API contracts for the admin control plane."""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class PlatformStatsOut(BaseModel):
    total_users: int = 0
    total_threads: int = 0
    total_steps: int = 0
    total_files: int = 0
    active_workflows: int = 0


class TenantOut(BaseModel):
    tenant_id: str
    name: str
    status: str = "active"
    user_count: int = 0
    thread_count: int = 0


class AuditEntry(BaseModel):
    event_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    timestamp: str
    details: Optional[Dict[str, Any]] = None
