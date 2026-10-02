"""substrate.integrations.tools — built-in tools for ReActAgent + protocol bridges.

Tools are grouped by domain:
  mcp/          — Model Context Protocol client/tool bridge
  web/          — search, surf, read_url, wikipedia
  communication/— email_sender, http_request
  compute/      — calculator
  utils/        — current_time, tool_search
  ai/           — image_generator
  (root)        — memory, human_input
  task_manager/ — Kanban board
  code_interpreter/ — sandboxed code execution
  chain/        — ToolChainTool (sandboxed code-mode tool chaining)
  skills/       — SKILL.md prompt-skill packages

Quick-start::

    from substrate.integrations.tools import CalculatorTool, WebSearchTool
    agent = ReActAgent("bot", runtime, model=llm, tools=[CalculatorTool(), WebSearchTool()])
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# A tool is imported when it is asked for, not when the package is: each one needs its own extra (``mcp``, ``web``,
# ``code``…), and using the calculator must not require the web-search stack to be installed.
_LAZY: dict[str, str] = {
    "MCPClient": "substrate.integrations.tools.mcp",
    "MCPTool": "substrate.integrations.tools.mcp",
    "CalculatorTool": "substrate.integrations.tools.compute.calculator",
    "CurrentTimeTool": "substrate.integrations.tools.utils.current_time",
    "WebSearchTool": "substrate.integrations.tools.web.search",
    "ReadUrlTool": "substrate.integrations.tools.web.read_url",
    "WikipediaTool": "substrate.integrations.tools.web.wikipedia",
    "WebSurferTool": "substrate.integrations.tools.web.surfer",
    "ToolChainTool": "substrate.integrations.tools.chain",
    "EmailSenderTool": "substrate.integrations.tools.communication.email_sender",
    "HttpRequestTool": "substrate.integrations.tools.communication.http_request",
    "AskHumanTool": "substrate.integrations.tools.human_input",
    "ImageGeneratorTool": "substrate.integrations.tools.ai.image_generator",
    "MemoryTool": "substrate.integrations.tools.memory",
    "PipelineManagerTool": "substrate.integrations.tools.pipeline_manager",
    "TaskManagerTool": "substrate.integrations.tools.task_manager.tool",
    "ToolSearchTool": "substrate.integrations.tools.utils.tool_search",
}

if TYPE_CHECKING:
    from substrate.integrations.tools.mcp import MCPClient
    from substrate.integrations.tools.mcp import MCPTool
    from substrate.integrations.tools.compute.calculator import CalculatorTool
    from substrate.integrations.tools.utils.current_time import CurrentTimeTool
    from substrate.integrations.tools.web.search import WebSearchTool
    from substrate.integrations.tools.web.read_url import ReadUrlTool
    from substrate.integrations.tools.web.wikipedia import WikipediaTool
    from substrate.integrations.tools.web.surfer import WebSurferTool
    from substrate.integrations.tools.chain import ToolChainTool
    from substrate.integrations.tools.communication.email_sender import EmailSenderTool
    from substrate.integrations.tools.communication.http_request import HttpRequestTool
    from substrate.integrations.tools.human_input import AskHumanTool
    from substrate.integrations.tools.ai.image_generator import ImageGeneratorTool
    from substrate.integrations.tools.memory import MemoryTool
    from substrate.integrations.tools.pipeline_manager import PipelineManagerTool
    from substrate.integrations.tools.task_manager.tool import TaskManagerTool
    from substrate.integrations.tools.utils.tool_search import ToolSearchTool

# CodeInterpreterTool is intentionally NOT exported here.
# It executes arbitrary code in a sandboxed VM and requires explicit opt-in
# by the caller: from substrate.integrations.tools.code_interpreter.tool import CodeInterpreterTool

__all__ = [
    "MCPClient",
    "MCPTool",
    "CalculatorTool",
    "CurrentTimeTool",
    "WebSearchTool",
    "ReadUrlTool",
    "WikipediaTool",
    "WebSurferTool",
    "ToolChainTool",
    "EmailSenderTool",
    "HttpRequestTool",
    "AskHumanTool",
    "ImageGeneratorTool",
    "MemoryTool",
    "PipelineManagerTool",
    "TaskManagerTool",
    "ToolSearchTool",
]


def __getattr__(name: str) -> object:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
