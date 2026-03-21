"""Shared API contracts for tool executor and MCP registry."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ToolCallRequest(BaseModel):
    """Request to execute a tool via the Tool Executor service."""
    run_id: str
    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    risk_tier: str = "safe"  # safe, sensitive, critical
    timeout_seconds: float = 300.0
    tenant_id: str = "default"
    workspace_id: str = "default"


class ToolCallResult(BaseModel):
    """Result from a tool execution."""
    tool_call_id: str
    tool_name: str
    content: str = ""
    is_error: bool = False
    metadata: Optional[Dict[str, Any]] = None
    app_data: Optional[Dict[str, Any]] = None
    execution_ms: Optional[int] = None


class ToolSchema(BaseModel):
    """Tool registration schema."""
    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    risk: str = "safe"
    category: str = "general"
    meta: Optional[Dict[str, Any]] = None


class McpAppRegistration(BaseModel):
    """MCP App registration in the registry."""
    app_id: str
    name: str
    description: str = ""
    capabilities: List[str] = Field(default_factory=list)
    resource_uri: str = ""
    version: str = "1.0.0"
    status: str = "active"
