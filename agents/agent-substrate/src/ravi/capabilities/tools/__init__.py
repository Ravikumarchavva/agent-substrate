"""ravi.capabilities.tools — built-in tools for ReActAgent.

Tools are grouped by domain:
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

Quick-start::

    from ravi.capabilities.tools import CalculatorTool, WebSearchTool
    agent = ReActAgent("bot", runtime, model=llm, tools=[CalculatorTool(), WebSearchTool()])
"""

from __future__ import annotations

from ravi.capabilities.tools.compute.calculator import CalculatorTool
from ravi.capabilities.tools.utils.current_time import CurrentTimeTool
from ravi.capabilities.tools.web.search import WebSearchTool
from ravi.capabilities.tools.web.read_url import ReadUrlTool
from ravi.capabilities.tools.web.wikipedia import WikipediaTool
from ravi.capabilities.tools.web.surfer import WebSurferTool
from ravi.capabilities.tools.chain import ToolChainTool
from ravi.capabilities.tools.files.document_analyzer import DocumentAnalyzerTool
from ravi.capabilities.tools.communication.email_sender import EmailSenderTool
from ravi.capabilities.tools.communication.http_request import HttpRequestTool
from ravi.capabilities.tools.human_input import AskHumanTool, ToolApprovalHandler
from ravi.capabilities.tools.ai.image_generator import ImageGeneratorTool
from ravi.capabilities.tools.files.invoice_extractor import InvoiceExtractorTool
from ravi.capabilities.tools.ai.knowledge_search import KnowledgeSearchTool
from ravi.capabilities.tools.memory import MemoryTool
from ravi.capabilities.tools.pipeline_manager import PipelineManagerTool
from ravi.capabilities.tools.task_manager.tool import TaskManagerTool
from ravi.capabilities.tools.utils.tool_search import ToolSearchTool

# CodeInterpreterTool is intentionally NOT exported here.
# It executes arbitrary code in a sandboxed VM and requires explicit opt-in
# by the caller: from ravi.capabilities.tools.code_interpreter.tool import CodeInterpreterTool

__all__ = [
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
    "ToolApprovalHandler",
    "ImageGeneratorTool",
    "InvoiceExtractorTool",
    "KnowledgeSearchTool",
    "MemoryTool",
    "PipelineManagerTool",
    "TaskManagerTool",
    "ToolSearchTool",
]
