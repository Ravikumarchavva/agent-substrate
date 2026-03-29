"""ChainExecutorTool — LLM-facing tool for executing adapter chain scripts.

The agent writes Python code that chains multiple adapters together and
submits it to this tool for execution.  The script runs with adapter
proxies injected so ``await adapters.name(...)`` calls work.
"""

from __future__ import annotations

from typing import Any

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk


class ChainExecutorTool(BaseTool):
    """Execute a Python script that chains multiple adapters together."""

    def __init__(self, chain_runtime: Any) -> None:
        from raavan.catalog._chain_runtime import ChainRuntime

        if not isinstance(chain_runtime, ChainRuntime):
            raise TypeError(
                f"Expected ChainRuntime, got {type(chain_runtime).__name__}"
            )
        self._runtime: ChainRuntime = chain_runtime
        super().__init__(
            name="chain_executor",
            description=(
                "Execute a Python script that chains multiple adapters/tools together. "
                "The script has access to 'adapters' namespace — call any tool via "
                "'result = await adapters.tool_name(param=value)'. "
                "Use 'results.append(value)' to collect outputs. "
                "Large data flows automatically via DataRef pointers."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python script to execute. Use 'await adapters.tool_name(...)' "
                            "to call tools. Use 'results.append(...)' to return data. "
                            "Use 'print(...)' for logging."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this chain does",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max execution time in seconds (default: 120)",
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SENSITIVE,
            category="development/execution",
            tags=["chain", "pipeline", "script", "execute", "compose", "workflow"],
            aliases=["run_chain", "execute_chain", "compose_tools"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        code: str,
        description: str = "",
        timeout: int = 120,
    ) -> ToolResult:
        """Execute the chain script via ChainRuntime."""
        from raavan.catalog._chain_runtime import ChainResult

        result: ChainResult = await self._runtime.execute_script(code, timeout=timeout)

        if result.error:
            error_text = f"Chain execution failed: {result.error}"
            if result.logs:
                error_text += f"\n\nLogs:\n{result.logs}"
            return ToolResult(
                content=[{"type": "text", "text": error_text}],
                is_error=True,
            )

        # Build response
        parts = []
        if result.logs:
            parts.append(f"Logs:\n{result.logs}")

        if result.outputs:
            parts.append(f"Outputs ({len(result.outputs)}):")
            for i, output in enumerate(result.outputs):
                parts.append(f"  [{i}]: {output}")

        if result.data_refs:
            parts.append(f"DataRefs ({len(result.data_refs)}):")
            for ref in result.data_refs:
                parts.append(f"  - {ref.summary()}")

        parts.append(f"Duration: {result.duration_ms}ms")

        return ToolResult(
            content=[{"type": "text", "text": "\n".join(parts)}],
        )
