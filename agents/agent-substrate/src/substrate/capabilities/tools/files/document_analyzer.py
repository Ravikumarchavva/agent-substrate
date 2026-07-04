"""DocumentAnalyzerTool — extract and analyze text from uploaded documents.

Reads plain-text and Markdown files, extracts their content, and (optionally)
produces an LLM-powered summary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel import TextBlock
from substrate.logger import setup_logging

logger = setup_logging()


class DocumentAnalyzerTool:
    """Parse and analyze document content with optional summarization."""

    name = "document_analyzer"
    description = "Extract text from a file and optionally summarize it or answer a question about it."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to analyze.",
            },
            "action": {
                "type": "string",
                "enum": ["extract", "summarize", "question"],
                "description": "extract — return raw text; summarize — LLM summary; question — answer a question.",
            },
            "question": {
                "type": "string",
                "description": "Question to answer (required when action=question).",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def __init__(self, model_client: Any = None) -> None:
        self._model_client = model_client

    async def execute(  # type: ignore[override]
        self,
        *,
        file_path: str,
        action: str = "extract",
        question: str = "",
        **_: object,
    ) -> ToolExecutionResult:
        path = Path(file_path)
        if not path.exists():
            return ToolExecutionResult(
                content=[TextBlock(text=f"File not found: {file_path}")],
                is_error=True,
            )

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Error reading file: {exc}")],
                is_error=True,
            )

        max_chars = 50_000
        truncated = len(content) > max_chars
        display_content = content[:max_chars]
        if truncated:
            display_content += f"\n\n... [truncated, total {len(content)} chars]"

        if action == "extract":
            return ToolExecutionResult(
                content=[TextBlock(text=display_content)],
                structured_content={
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

            from substrate.kernel import ChatMessage, TextBlock as _TB

            messages = [ChatMessage(role="user", content=[_TB(text=user_msg)])]
            from substrate.kernel.llm import GenerationOptions

            response = await self._model_client.generate(
                messages, options=GenerationOptions(system_instructions=system)
            )
            answer = ""
            if response:
                answer = " ".join(
                    getattr(b, "text", "") for b in response if hasattr(b, "text")
                )
            return ToolExecutionResult(
                content=[TextBlock(text=answer or "No response generated.")],
                structured_content={"file": str(path), "action": action},
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )
