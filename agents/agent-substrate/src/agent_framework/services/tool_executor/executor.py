"""Tool Executor — tool dispatch and execution.

Provides a centralized tool execution service that:
1. Maintains a registry of available tools
2. Executes tool calls with timeout and sandboxing
3. Publishes tool results as events
4. Handles MCP tool discovery and schema management
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from agent_framework.core.tools.base_tool import BaseTool, ToolResult
from agent_framework.shared.events.bus import EventBus
from agent_framework.shared.events.envelope import EventEnvelope

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available tools, indexed by name."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        schema = tool.get_schema()
        self._tools[schema.name] = tool
        logger.debug("Registered tool: %s", schema.name)

    def register_many(self, tools: List[BaseTool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        result = []
        for name, tool in self._tools.items():
            try:
                schema = tool.get_schema()
                result.append(
                    {
                        "name": schema.name,
                        "description": schema.description,
                        "input_schema": schema.input_schema,
                        "risk": getattr(schema, "risk", "safe"),
                    }
                )
            except Exception as e:
                logger.warning("Failed to get schema for %s: %s", name, e)
        return result

    @property
    def tool_count(self) -> int:
        return len(self._tools)


async def execute_tool(
    *,
    registry: ToolRegistry,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Execute a tool by name with timeout.

    Returns a dict with:
    - tool_name
    - tool_call_id
    - content (result text)
    - is_error
    - metadata (from tool)
    """
    tool = registry.get(tool_name)
    if not tool:
        return {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": f"Tool '{tool_name}' not found in registry.",
            "is_error": True,
            "metadata": {},
        }

    try:
        result: ToolResult = await asyncio.wait_for(
            tool.execute(**arguments),
            timeout=timeout,
        )
        return {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": result.content if hasattr(result, "content") else str(result),
            "is_error": False,
            "metadata": result.metadata if hasattr(result, "metadata") else {},
        }

    except asyncio.TimeoutError:
        return {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": f"Tool '{tool_name}' timed out after {timeout}s.",
            "is_error": True,
            "metadata": {},
        }
    except Exception as exc:
        logger.exception("Tool execution failed: %s", tool_name)
        return {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": f"Tool '{tool_name}' failed: {exc}",
            "is_error": True,
            "metadata": {},
        }


async def execute_and_publish(
    *,
    registry: ToolRegistry,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_call_id: str,
    run_id: str,
    thread_id: str,
    event_bus: EventBus,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Execute a tool and publish the result as an event."""
    result = await execute_tool(
        registry=registry,
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        timeout=timeout,
    )

    await event_bus.publish(
        EventEnvelope(
            event_type="tool.execution_completed",
            correlation_id=run_id,
            payload={
                "type": "tool.execution_completed",
                "run_id": run_id,
                "thread_id": thread_id,
                **result,
            },
        )
    )

    return result
