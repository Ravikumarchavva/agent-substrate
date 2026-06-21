"""Pipeline engine — declarative chains of adapter steps.

A ``PipelineDef`` is a named, reusable sequence of adapter calls with
input/output mappings.  Pipelines can be created by the LLM, saved
by the user, and re-executed on demand or by triggers.

Usage::

    engine = PipelineEngine(registry=toolbox, data_store=store)
    pipeline = PipelineDef(
        name="daily-report",
        steps=[
            PipelineStep(adapter_name="postgres_query", action="query",
                         input_mapping={"sql": "SELECT ..."}),
            PipelineStep(adapter_name="email_sender", action="execute",
                         input_mapping={"to": "user@example.com",
                                        "subject": "Daily Report",
                                        "body": "$prev.result"}),
        ],
    )
    result = await engine.execute(pipeline)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from substrate.capabilities.pipeline.data_ref import DataRefStore
from substrate.agents.tools.toolbox import Toolbox
from substrate.logger import setup_logging

logger = setup_logging()


@dataclass
class PipelineStep:
    """A single step in a pipeline."""

    adapter_name: str
    action: str = "execute"
    input_mapping: Dict[str, Any] = field(default_factory=dict)
    output_key: str = ""
    timeout: int = 60


@dataclass
class PipelineDef:
    """A named, saved pipeline definition."""

    name: str
    description: str = ""
    steps: List[PipelineStep] = field(default_factory=list)
    created_by: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [
                {
                    "adapter_name": s.adapter_name,
                    "action": s.action,
                    "input_mapping": s.input_mapping,
                    "output_key": s.output_key,
                    "timeout": s.timeout,
                }
                for s in self.steps
            ],
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> PipelineDef:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            steps=[
                PipelineStep(
                    adapter_name=s["adapter_name"],
                    action=s.get("action", "execute"),
                    input_mapping=s.get("input_mapping", {}),
                    output_key=s.get("output_key", ""),
                    timeout=s.get("timeout", 60),
                )
                for s in d.get("steps", [])
            ],
            created_by=d.get("created_by", ""),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
        )


@dataclass
class PipelineResult:
    """Result of executing a pipeline."""

    pipeline_name: str
    success: bool = True
    step_results: List[Dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class PipelineEngine:
    """Execute pipelines as a sequence of adapter steps."""

    def __init__(
        self,
        registry: Toolbox,
        data_store: DataRefStore | None = None,
    ) -> None:
        self._registry = registry
        self._data_store = data_store

    async def execute(self, pipeline: PipelineDef) -> PipelineResult:
        """Execute all steps in order, passing data via context dict."""
        start = time.monotonic()
        context: Dict[str, Any] = {}
        step_results: List[Dict[str, Any]] = []

        for i, step in enumerate(pipeline.steps):
            tool = self._registry.get(step.adapter_name)
            if tool is None:
                duration = int((time.monotonic() - start) * 1000)
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=False,
                    step_results=step_results,
                    error=f"Step {i}: adapter '{step.adapter_name}' not found",
                    duration_ms=duration,
                )

            resolved_inputs = self._resolve_inputs(step.input_mapping, context)

            try:
                result = await tool.execute(**resolved_inputs)
            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=False,
                    step_results=step_results,
                    error=f"Step {i} ({step.adapter_name}): {exc}",
                    duration_ms=duration,
                )

            output_key = step.output_key or f"step_{i}"
            step_output = {
                "adapter": step.adapter_name,
                "content": result.text,
                "is_error": result.is_error,
            }
            context[output_key] = step_output
            context["prev"] = step_output
            step_results.append(step_output)

            if result.is_error:
                duration = int((time.monotonic() - start) * 1000)
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=False,
                    step_results=step_results,
                    error=f"Step {i} ({step.adapter_name}) returned error",
                    duration_ms=duration,
                )

        duration = int((time.monotonic() - start) * 1000)
        return PipelineResult(
            pipeline_name=pipeline.name,
            success=True,
            step_results=step_results,
            duration_ms=duration,
        )

    def validate(self, pipeline: PipelineDef) -> list[str]:
        """Validate a pipeline definition. Returns a list of errors."""
        errors: list[str] = []
        for i, step in enumerate(pipeline.steps):
            if not step.adapter_name:
                errors.append(f"Step {i}: missing adapter_name")
            elif self._registry.get(step.adapter_name) is None:
                errors.append(
                    f"Step {i}: adapter '{step.adapter_name}' not found in registry"
                )
        return errors

    @staticmethod
    def _resolve_inputs(
        mapping: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve $-references in input_mapping values.

        Supports ``$prev.content`` and ``$context.step_0.content`` references.
        Literal values pass through unchanged.
        """
        resolved: Dict[str, Any] = {}
        for key, value in mapping.items():
            if isinstance(value, str) and value.startswith("$"):
                parts = value[1:].split(".")
                obj: Any = context
                for part in parts:
                    if isinstance(obj, dict):
                        obj = obj.get(part)
                    else:
                        obj = None
                        break
                resolved[key] = _extract_text(obj)
            else:
                resolved[key] = value
        return resolved


def _extract_text(value: Any) -> Any:
    """If *value* is a ToolResult content list, return the joined text."""
    if (
        isinstance(value, list)
        and value
        and isinstance(value[0], dict)
        and "text" in value[0]
    ):
        texts = [
            item["text"] for item in value if isinstance(item, dict) and "text" in item
        ]
        return "\n".join(texts) if len(texts) > 1 else texts[0]
    return value
