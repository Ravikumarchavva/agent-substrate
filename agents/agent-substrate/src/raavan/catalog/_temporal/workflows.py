"""Temporal workflows for pipeline and chain execution."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from raavan.catalog._temporal.activities import (
        execute_adapter_step,
        execute_code_chain,
    )

logger = logging.getLogger(__name__)


@workflow.defn
class PipelineWorkflow:
    """Durable workflow that executes a PipelineDef as sequential Temporal activities."""

    @workflow.run
    async def run(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Execute all pipeline steps as activities.

        Args:
            definition: Serialised PipelineDef dict with 'name', 'steps', etc.
        """
        steps = definition.get("steps", [])
        name = definition.get("name", "unnamed")
        results: list[dict[str, Any]] = []
        prev_result: dict[str, Any] | None = None

        workflow.logger.info(
            "PipelineWorkflow '%s' started with %d steps", name, len(steps)
        )

        for i, step in enumerate(steps):
            # Resolve $prev references in inputs
            inputs = self._resolve_refs(
                step.get("input_mapping", {}), prev_result, results
            )

            step_input = {
                "adapter_name": step["adapter_name"],
                "action": step.get("action", "execute"),
                "inputs": inputs,
            }

            result = await workflow.execute_activity(
                execute_adapter_step,
                step_input,
                start_to_close_timeout=timedelta(seconds=step.get("timeout", 300)),
            )

            results.append(result)
            prev_result = result

            if not result.get("success", False):
                workflow.logger.error(
                    "Step %d (%s) failed: %s",
                    i,
                    step["adapter_name"],
                    result.get("error"),
                )
                return {
                    "pipeline": name,
                    "success": False,
                    "completed_steps": i,
                    "total_steps": len(steps),
                    "error": result.get("error"),
                    "step_results": results,
                }

        workflow.logger.info(
            "PipelineWorkflow '%s' completed all %d steps", name, len(steps)
        )
        return {
            "pipeline": name,
            "success": True,
            "completed_steps": len(steps),
            "total_steps": len(steps),
            "step_results": results,
        }

    @staticmethod
    def _resolve_refs(
        mapping: dict[str, Any],
        prev: dict[str, Any] | None,
        all_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Replace $prev.field and $step[n].field references with actual values."""
        resolved: dict[str, Any] = {}
        for key, value in mapping.items():
            if (
                isinstance(value, str)
                and value.startswith("$prev.")
                and prev is not None
            ):
                field = value[len("$prev.") :]
                resolved[key] = prev.get(field, value)
            elif isinstance(value, str) and value.startswith("$step["):
                # $step[0].content → all_results[0]["content"]
                try:
                    bracket_end = value.index("]")
                    idx = int(value[6:bracket_end])
                    field = value[bracket_end + 2 :]  # skip ].
                    resolved[key] = all_results[idx].get(field, value)
                except (ValueError, IndexError):
                    resolved[key] = value
            else:
                resolved[key] = value
        return resolved


@workflow.defn
class ChainWorkflow:
    """Durable workflow that executes LLM-written code via ChainRuntime."""

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a code chain as a Temporal activity.

        Args:
            params: Dict with keys: code, description, timeout
        """
        code = params["code"]
        timeout = params.get("timeout", 120)

        workflow.logger.info("ChainWorkflow started (timeout=%ds)", timeout)

        result = await workflow.execute_activity(
            execute_code_chain,
            {"code": code, "timeout": timeout},
            start_to_close_timeout=timedelta(seconds=timeout + 30),
        )

        return result
