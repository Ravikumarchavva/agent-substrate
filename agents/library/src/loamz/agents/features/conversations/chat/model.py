from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    role: str = "user"
    content: str = ""
    attachments: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    thread_id: str
    messages: list[ChatMessageIn] = Field(default_factory=list)
    model: str | None = None
    agent_id: str | None = None
    attachments: list[dict[str, object]] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    system_instructions: str | None = None
    prompt: str | None = None

    def prompt_text(self) -> str:
        if self.prompt:
            return self.prompt
        for m in reversed(self.messages):
            if m.role == "user" and m.content.strip():
                return m.content
        return ""


class HitlResponse(BaseModel):
    action: str = "approve"
    modified_args: dict[str, object] | None = None
    reason: str | None = None


class HitlStatus(BaseModel):
    status: str = "idle"
    pending: list[dict[str, object]] = Field(default_factory=list)


class ApprovalOut(BaseModel):
    request_id: str
    thread_id: str
    run_id: str
    tool_name: str
    tool_args: dict[str, object] = Field(default_factory=dict)
    requested_at: str | None = None


class CancelResponse(BaseModel):
    thread_id: str
    cancelled: bool = True
