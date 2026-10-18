from __future__ import annotations

from pydantic import BaseModel, Field


class PipelineStep(BaseModel):
    id: str = ""
    adapter: str = ""
    action: str = ""
    params: dict[str, object] = Field(default_factory=dict)


class PipelineDef(BaseModel):
    name: str
    description: str = ""
    steps: list[PipelineStep] = Field(default_factory=list)
    created_by: str = ""
    created_at: float | None = None
    updated_at: float | None = None


class PipelineRunResult(BaseModel):
    pipeline: str
    success: bool
    step_count: int
    duration_ms: int
    error: str | None = None


class PipelineValidationResult(BaseModel):
    pipeline: str
    valid: bool
    errors: list[str] = Field(default_factory=list)

