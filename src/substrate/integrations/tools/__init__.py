"""substrate.integrations.tools — built-in tools for ReActAgent + protocol bridges.

Tools are grouped by domain:
  mcp/          — Model Context Protocol client/tool bridge
  web/          — search, surf, read_url, wikipedia
  files/        — document_analyzer, invoice_extractor
  communication/— email_sender, http_request
  compute/      — calculator
  utils/        — current_time, tool_search
  ai/           — image_generator, knowledge_search
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

from substrate.integrations.tools.mcp import MCPClient, MCPTool
from substrate.integrations.tools.compute.calculator import CalculatorTool
from substrate.integrations.tools.utils.current_time import CurrentTimeTool
from substrate.integrations.tools.web.search import WebSearchTool
from substrate.integrations.tools.web.read_url import ReadUrlTool
from substrate.integrations.tools.web.wikipedia import WikipediaTool
from substrate.integrations.tools.web.surfer import WebSurferTool
from substrate.integrations.tools.chain import ToolChainTool
from substrate.integrations.tools.files.document_analyzer import DocumentAnalyzerTool
from substrate.integrations.tools.communication.email_sender import EmailSenderTool
from substrate.integrations.tools.communication.http_request import HttpRequestTool
from substrate.integrations.tools.human_input import AskHumanTool
from substrate.integrations.tools.ai.image_generator import ImageGeneratorTool
from substrate.integrations.tools.files.invoice_extractor import InvoiceExtractorTool
from substrate.integrations.tools.ai.knowledge_search import KnowledgeSearchTool
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
    "DocumentAnalyzerTool",
    "EmailSenderTool",
    "HttpRequestTool",
    "AskHumanTool",
    "ImageGeneratorTool",
    "InvoiceExtractorTool",
    "KnowledgeSearchTool",
    "MemoryTool",
    "PipelineManagerTool",
    "TaskManagerTool",
    "ToolSearchTool",
]
