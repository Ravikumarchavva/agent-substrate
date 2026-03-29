"""Pipeline engine — saved declarative chains of adapter steps.

A ``PipelineDef`` is a named, reusable sequence of adapter calls with
input/output mappings.  Pipelines can be created by the LLM, saved
by the user, and re-executed on demand or by triggers.

Usage::

    engine = PipelineEngine(catalog=catalog, data_store=store)
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

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from raavan.catalog._data_ref import DataRef, DataRefStore

logger = logging.getLogger("raavan.catalog.pipeline")


@dataclass
class PipelineStep:
    """A single step in a pipeline."""

    adapter_name: str
    action: str = "execute"
    input_mapping: Dict[str, Any] = field(default_factory=dict)
    output_key: str = ""  # key to store result under for next steps
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
        """Serialise for JSON storage."""
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
        """Reconstruct from a serialised dict."""
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
    data_refs: List[DataRef] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0


class PipelineEngine:
    """Execute pipelines as a sequence of adapter steps.

    Parameters
    ----------
    catalog
        CapabilityRegistry to look up adapters.
    data_store
        DataRefStore for passing data between steps.
    """

    def __init__(
        self,
        catalog: Any,
        data_store: Optional[DataRefStore] = None,
    ) -> None:
        from raavan.core.tools.catalog import CapabilityRegistry

        self._catalog: CapabilityRegistry = catalog
        self._data_store = data_store

    async def execute(self, pipeline: PipelineDef) -> PipelineResult:
        """Execute all steps in order, passing data via context dict."""
        start = time.monotonic()
        context: Dict[str, Any] = {}
        step_results: List[Dict[str, Any]] = []
        data_refs: List[DataRef] = []

        for i, step in enumerate(pipeline.steps):
            tool = self._catalog.get_tool(step.adapter_name)
            if tool is None:
                duration = int((time.monotonic() - start) * 1000)
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=False,
                    step_results=step_results,
                    error=f"Step {i}: adapter '{step.adapter_name}' not found",
                    duration_ms=duration,
                )

            # Resolve input_mapping — substitute $prev.*, $context.* references
            resolved_inputs = self._resolve_inputs(step.input_mapping, context)

            try:
                result = await tool.run(**resolved_inputs)
            except Exception as exc:
                duration = int((time.monotonic() - start) * 1000)
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=False,
                    step_results=step_results,
                    error=f"Step {i} ({step.adapter_name}): {exc}",
                    duration_ms=duration,
                )

            # Store result in context
            output_key = step.output_key or f"step_{i}"
            step_output = {
                "adapter": step.adapter_name,
                "content": result.content,
                "is_error": result.is_error,
            }

            if result.data_ref is not None:
                step_output["data_ref"] = result.data_ref.to_dict()
                data_refs.append(result.data_ref)

            context[output_key] = step_output
            context["prev"] = step_output
            step_results.append(step_output)

            if result.is_error:
                duration = int((time.monotonic() - start) * 1000)
                return PipelineResult(
                    pipeline_name=pipeline.name,
                    success=False,
                    step_results=step_results,
                    data_refs=data_refs,
                    error=f"Step {i} ({step.adapter_name}) returned error",
                    duration_ms=duration,
                )

        duration = int((time.monotonic() - start) * 1000)
        return PipelineResult(
            pipeline_name=pipeline.name,
            success=True,
            step_results=step_results,
            data_refs=data_refs,
            duration_ms=duration,
        )

    def validate(self, pipeline: PipelineDef) -> List[str]:
        """Validate a pipeline definition.  Returns a list of errors."""
        errors: List[str] = []
        for i, step in enumerate(pipeline.steps):
            if not step.adapter_name:
                errors.append(f"Step {i}: missing adapter_name")
            elif self._catalog.get_tool(step.adapter_name) is None:
                errors.append(
                    f"Step {i}: adapter '{step.adapter_name}' not found in catalog"
                )
        return errors

    @staticmethod
    def _resolve_inputs(
        mapping: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve $-references in input_mapping values.

        Supports:
        - ``$prev.content`` — previous step's content
        - ``$context.step_0.content`` — named step output
        - literal values pass through unchanged

        When the resolved value is a ToolResult content list
        (``[{"type": "text", "text": "..."}]``), the text is extracted
        automatically so downstream adapters receive a plain string.
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


class PipelineStore:
    """Persist pipeline definitions in Postgres.

    Uses the SQLAlchemy async session for CRUD operations on the
    ``AdapterPipeline`` ORM model.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def save(self, pipeline: PipelineDef) -> None:
        """Save or update a pipeline definition."""
        from raavan.server.models import AdapterPipeline

        async with self._session_factory() as session:
            # Check for existing
            from sqlalchemy import select

            stmt = select(AdapterPipeline).where(AdapterPipeline.name == pipeline.name)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                existing.definition_json = json.dumps(pipeline.to_dict())
                existing.description = pipeline.description
            else:
                row = AdapterPipeline(
                    name=pipeline.name,
                    description=pipeline.description,
                    definition_json=json.dumps(pipeline.to_dict()),
                    created_by=pipeline.created_by,
                )
                session.add(row)

            await session.commit()

    async def load(self, name: str) -> Optional[PipelineDef]:
        """Load a pipeline by name."""
        from raavan.server.models import AdapterPipeline

        async with self._session_factory() as session:
            from sqlalchemy import select

            stmt = select(AdapterPipeline).where(AdapterPipeline.name == name)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return PipelineDef.from_dict(json.loads(row.definition_json))

    async def list_all(self) -> List[PipelineDef]:
        """List all saved pipelines."""
        from raavan.server.models import AdapterPipeline

        async with self._session_factory() as session:
            from sqlalchemy import select

            stmt = select(AdapterPipeline).order_by(AdapterPipeline.created_at)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                PipelineDef.from_dict(json.loads(row.definition_json)) for row in rows
            ]

    async def delete(self, name: str) -> bool:
        """Delete a pipeline by name.  Returns True if found and deleted."""
        from raavan.server.models import AdapterPipeline

        async with self._session_factory() as session:
            from sqlalchemy import delete as sql_delete

            stmt = sql_delete(AdapterPipeline).where(AdapterPipeline.name == name)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0  # type: ignore[union-attr]
