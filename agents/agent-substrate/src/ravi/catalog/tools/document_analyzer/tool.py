"""DocumentAnalyzerTool — extract and analyze text from uploaded documents.

Reads plain-text and Markdown files, extracts their content, and (optionally)
produces an LLM-powered summary.
"""

from __future__ import annotations
from ravi.logger import setup_logging

from pathlib import Path
from typing import Any

from ravi.kernel.tools import ToolExecutionResult
from ravi.kernel import TextBlock

logger = setup_logging()


class DocumentAnalyzerTool:
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
    ) -> ToolExecutionResult:
        path = Path(file_path)
        if not path.exists():
            return ToolExecutionResult(
                content=[TextBlock(text=f"File not found: {file_path}")],
                is_error=True,
            )

        # Read file content
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Error reading file: {exc}")],
                is_error=True,
            )

        # Truncate very large files
        max_chars = 50_000
        truncated = len(content) > max_chars
        display_content = content[:max_chars]
        if truncated:
            display_content += f"\n\n... [truncated, total {len(content)} chars]"

        if action == "extract":
            return ToolExecutionResult(
                content=[TextBlock(text=display_content)],
                app_data={
                    "file": str(path),
                    "chars": len(content),
                    "truncated": truncated,
                },
            )

        if action in ("summarize", "question"):
            if self._model_client is None:
                return ToolExecutionResult(
                    content=[
                        TextBlock(
                            text=f"LLM not configured for {action}. Here is the raw content:\n\n{display_content}"
                        )
                    ],
                )

            if action == "summarize":
                system = "Summarize the following document concisely."
                user_msg = display_content
            else:
                if not question.strip():
                    return ToolExecutionResult(
                        content=[
                            TextBlock(
                                text="Please provide a 'question' for the question action."
                            )
                        ],
                        is_error=True,
                    )
                system = "Answer the user's question based on the document content."
                user_msg = f"Document:\n{display_content}\n\nQuestion: {question}"

            from ravi.kernel import ChatMessage, TextBlock as _TB

            messages = [ChatMessage(role="user", content=[_TB(text=user_msg)])]
            response = await self._model_client.generate(
                messages,
                system=system,
            )
            answer = ""
            if response:
                answer = " ".join(getattr(b, "text", "") for b in response if hasattr(b, "text"))
            return ToolExecutionResult(
                content=[TextBlock(text=answer or "No response generated.")],
                app_data={"file": str(path), "action": action},
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )
