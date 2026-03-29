"""DocumentAnalyzerTool — extract and analyze text from uploaded documents.

Reads plain-text and Markdown files, extracts their content, and (optionally)
produces an LLM-powered summary.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk

logger = logging.getLogger(__name__)


class DocumentAnalyzerTool(BaseTool):
    """Parse and analyze document content with optional summarization."""

    def __init__(self, model_client: Any = None) -> None:
        self._model_client = model_client
        super().__init__(
            name="document_analyzer",
            description=(
                "Analyze a document: extract full text, produce a summary, "
                "or answer questions about the content."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the document file to analyze",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["extract", "summarize", "question"],
                        "description": (
                            "Action: extract (full text), summarize, or "
                            "question (answer a question about the doc)"
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": "Question to answer about the document (for action='question')",
                    },
                },
                "required": ["file_path", "action"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SAFE,
            category="data/exploration",
            tags=[
                "document",
                "pdf",
                "analyze",
                "extract",
                "summarize",
                "parse",
                "text",
            ],
            aliases=["doc_reader", "parse_document"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        file_path: str,
        action: str = "extract",
        question: str = "",
    ) -> ToolResult:
        path = Path(file_path)
        if not path.exists():
            return ToolResult(
                content=[{"type": "text", "text": f"File not found: {file_path}"}],
                is_error=True,
            )

        # Read file content
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult(
                content=[{"type": "text", "text": f"Error reading file: {exc}"}],
                is_error=True,
            )

        # Truncate very large files
        max_chars = 50_000
        truncated = len(content) > max_chars
        display_content = content[:max_chars]
        if truncated:
            display_content += f"\n\n... [truncated, total {len(content)} chars]"

        if action == "extract":
            return ToolResult(
                content=[{"type": "text", "text": display_content}],
                app_data={
                    "file": str(path),
                    "chars": len(content),
                    "truncated": truncated,
                },
            )

        if action in ("summarize", "question"):
            if self._model_client is None:
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": f"LLM not configured for {action}. Here is the raw content:\n\n{display_content}",
                        }
                    ],
                )

            if action == "summarize":
                system = "Summarize the following document concisely."
                user_msg = display_content
            else:
                if not question.strip():
                    return ToolResult(
                        content=[
                            {
                                "type": "text",
                                "text": "Please provide a 'question' for the question action.",
                            }
                        ],
                        is_error=True,
                    )
                system = "Answer the user's question based on the document content."
                user_msg = f"Document:\n{display_content}\n\nQuestion: {question}"

            from raavan.core.messages.client_messages import (
                SystemMessage,
                UserMessage,
            )

            messages = [SystemMessage(content=system), UserMessage(content=[user_msg])]
            response = await self._model_client.generate(messages)
            answer = ""
            if response.content:
                answer = " ".join(str(c) for c in response.content if c)
            return ToolResult(
                content=[{"type": "text", "text": answer or "No response generated."}],
                app_data={"file": str(path), "action": action},
            )

        return ToolResult(
            content=[{"type": "text", "text": f"Unknown action: {action!r}"}],
            is_error=True,
        )
