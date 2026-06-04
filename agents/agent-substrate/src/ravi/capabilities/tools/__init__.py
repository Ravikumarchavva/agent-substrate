"""ravi.capabilities.tools — built-in tools for ReActAgent.

Simple tools are flat modules; complex multi-file tools keep their folder.

Quick-start::

    from ravi.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool, WikipediaTool, ReadUrlTool
    agent = ReActAgent("bot", runtime, model=llm, tools=[CalculatorTool(), WebSearchTool()])
"""

from __future__ import annotations

# Built-in utility tools (no API key, no extra dependencies)
from ravi.capabilities.tools.calculator import CalculatorTool
from ravi.capabilities.tools.current_time import CurrentTimeTool
from ravi.capabilities.tools.web_search import WebSearchTool
from ravi.capabilities.tools.read_url import ReadUrlTool
from ravi.capabilities.tools.wikipedia import WikipediaTool

# Capability tools
from ravi.capabilities.tools.chain_executor import ChainExecutorTool
from ravi.capabilities.tools.document_analyzer import DocumentAnalyzerTool
from ravi.capabilities.tools.email_sender import EmailSenderTool
from ravi.capabilities.tools.http_request import HttpRequestTool
from ravi.capabilities.tools.human_input import AskHumanTool, ToolApprovalHandler
from ravi.capabilities.tools.image_generator import ImageGeneratorTool
from ravi.capabilities.tools.invoice_extractor import InvoiceExtractorTool
from ravi.capabilities.tools.knowledge_search import KnowledgeSearchTool
from ravi.capabilities.tools.memory import MemoryTool
from ravi.capabilities.tools.pipeline_manager import PipelineManagerTool
from ravi.capabilities.tools.task_manager.tool import TaskManagerTool
from ravi.capabilities.tools.tool_search import ToolSearchTool
from ravi.capabilities.tools.web_surfer import WebSurferTool

__all__ = [
    # Built-ins (no API key needed)
    "CalculatorTool",
    "CurrentTimeTool",
    "WebSearchTool",
    "ReadUrlTool",
    "WikipediaTool",
    # Capabilities
    "ChainExecutorTool",
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
    "WebSurferTool",
]
