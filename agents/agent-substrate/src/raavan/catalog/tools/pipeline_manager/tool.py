"""PipelineManagerTool — LLM-facing tool for pipeline CRUD and execution."""

from __future__ import annotations

from typing import Any, Optional

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk


class PipelineManagerTool(BaseTool):
    """Manage and execute saved adapter pipelines."""

    def __init__(
        self,
        pipeline_engine: Any,
        pipeline_store: Any,
    ) -> None:
        self._engine = pipeline_engine
        self._store = pipeline_store
        super().__init__(
            name="pipeline_manager",
            description=(
                "Manage saved adapter pipelines: run, save, list, or delete "
                "reusable chains of adapter steps."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "run",
                            "save",
                            "list",
                            "delete",
                            "validate",
                        ],
                        "description": "Action to perform on pipelines",
                    },
                    "name": {
                        "type": "string",
                        "description": "Pipeline name (for run/save/delete)",
                    },
                    "definition": {
                        "type": "object",
                        "description": (
                            "Pipeline definition for save action: "
                            '{"description": "...", "steps": [{"adapter_name": "...", '
                            '"input_mapping": {...}}]}'
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SENSITIVE,
            category="development/execution",
            tags=["pipeline", "workflow", "chain", "automate", "sequence"],
            aliases=["manage_pipeline", "run_pipeline"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        name: str = "",
        definition: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        """Execute the pipeline management action."""
        if action == "run":
            return await self._run(name)
        if action == "save":
            return await self._save(name, definition)
        if action == "list":
            return await self._list()
        if action == "delete":
            return await self._delete(name)
        if action == "validate":
            return await self._validate(name)
        return ToolResult(
            content=[{"type": "text", "text": f"Unknown action: {action!r}"}],
            is_error=True,
        )

    async def _run(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(
                content=[{"type": "text", "text": "Pipeline name required for 'run'"}],
                is_error=True,
            )
        pipeline = await self._store.load(name)
        if pipeline is None:
            return ToolResult(
                content=[{"type": "text", "text": f"Pipeline '{name}' not found"}],
                is_error=True,
            )
        result = await self._engine.execute(pipeline)
        if not result.success:
            return ToolResult(
                content=[{"type": "text", "text": f"Pipeline failed: {result.error}"}],
                is_error=True,
            )
        parts = [f"Pipeline '{name}' completed in {result.duration_ms}ms"]
        for i, sr in enumerate(result.step_results):
            parts.append(
                f"  Step {i} ({sr['adapter']}): {'error' if sr['is_error'] else 'ok'}"
            )
        return ToolResult(content=[{"type": "text", "text": "\n".join(parts)}])

    async def _save(
        self, name: str, definition: Optional[dict[str, Any]]
    ) -> ToolResult:
        if not name or not definition:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Both 'name' and 'definition' required for save",
                    }
                ],
                is_error=True,
            )
        from raavan.catalog._pipeline import PipelineDef

        definition["name"] = name
        pipeline = PipelineDef.from_dict(definition)
        await self._store.save(pipeline)
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": f"Pipeline '{name}' saved ({len(pipeline.steps)} steps)",
                }
            ],
        )

    async def _list(self) -> ToolResult:
        pipelines = await self._store.list_all()
        if not pipelines:
            return ToolResult(
                content=[{"type": "text", "text": "No saved pipelines"}],
            )
        lines = [f"Saved pipelines ({len(pipelines)}):"]
        for p in pipelines:
            lines.append(
                f"  - {p.name}: {p.description or '(no description)'} ({len(p.steps)} steps)"
            )
        return ToolResult(content=[{"type": "text", "text": "\n".join(lines)}])

    async def _delete(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(
                content=[
                    {"type": "text", "text": "Pipeline name required for 'delete'"}
                ],
                is_error=True,
            )
        deleted = await self._store.delete(name)
        if deleted:
            return ToolResult(
                content=[{"type": "text", "text": f"Pipeline '{name}' deleted"}],
            )
        return ToolResult(
            content=[{"type": "text", "text": f"Pipeline '{name}' not found"}],
            is_error=True,
        )

    async def _validate(self, name: str) -> ToolResult:
        if not name:
            return ToolResult(
                content=[
                    {"type": "text", "text": "Pipeline name required for 'validate'"}
                ],
                is_error=True,
            )
        pipeline = await self._store.load(name)
        if pipeline is None:
            return ToolResult(
                content=[{"type": "text", "text": f"Pipeline '{name}' not found"}],
                is_error=True,
            )
        errors = self._engine.validate(pipeline)
        if errors:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Validation errors:\n"
                        + "\n".join(f"  - {e}" for e in errors),
                    }
                ],
                is_error=True,
            )
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": f"Pipeline '{name}' is valid ({len(pipeline.steps)} steps)",
                }
            ],
        )
