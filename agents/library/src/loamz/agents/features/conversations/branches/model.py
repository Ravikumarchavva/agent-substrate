from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from loamz.agents.kernel.types.content import JsonObject


class BranchOut(BaseModel):
    id: str
    session_id: str
    name: str | None = None
    head_message_id: str | None = None
    forked_from_message_id: str | None = None
    version: int = 0
    created_at: datetime


class BranchList(BaseModel):
    items: list[BranchOut]


class BranchForkRequest(BaseModel):
    new_branch_id: str
    source_branch_id: str = "main"
    fork_from_message_id: str | None = None


class BranchRenameRequest(BaseModel):
    name: str


class CheckpointOut(BaseModel):
    id: str
    session_id: str
    anchor_message_id: str
    summary: str
    state: JsonObject = Field(default_factory=dict)
    parent_checkpoint_id: str | None = None
    created_at: datetime


class CheckpointList(BaseModel):
    items: list[CheckpointOut]


class CheckpointCreateRequest(BaseModel):
    anchor_message_id: str
    summary: str
    state: JsonObject | None = None


class BranchMessages(BaseModel):
    items: list[dict]
