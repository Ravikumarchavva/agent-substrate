"""Shared response conversion for sandbox_runtime.py's /ci/run wire shape.

Every SandboxRuntime normalises its result to this dict shape (the in-pod
server's ``/ci/run`` body; local runtimes build it via
``ExecResult.to_sandbox_response``) — this is the one place it becomes a
ToolExecutionResult.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from substrate.kernel import ImageBlock, TextBlock
from substrate.kernel.tools import ToolExecutionResult

# Appended to both code-interpreter tools' descriptions. Teaches the model the
# ChatGPT-ADA presentation convention: files it saves in its working directory
# are addressable by the frontend via a ``sandbox:`` markdown scheme, so the
# model — not the tool — curates what the user actually sees, instead of every
# auto-captured figure flooding the chat.
PRESENTATION_GUIDANCE = (
    "\n\nPresenting results to the user: files you save in your working "
    "directory (e.g. plt.savefig('cost_variance.png'), df.to_csv('report.csv')) "
    "are shown to the user ONLY if you reference them in your final answer. To "
    "display a chart inline, write it as a markdown image: "
    "![short description](sandbox:cost_variance.png). To offer a file for "
    "download, write it as a markdown link: [Download report](sandbox:report.csv). "
    "Use a relative filename (it resolves against your working directory). Do "
    "NOT paste base64 or data URIs. Prefer saving a small number of clear, "
    "final figures over many exploratory ones; use os.listdir('.') to see what "
    "you've saved."
)


def sandbox_result_to_tool_result(result: dict[str, Any]) -> ToolExecutionResult:
    """Convert a sandbox_runtime.py ``/ci/run`` response dict → ToolResult.

    The sandbox runtime returns::

        {
            "status": "ok" | "error",
            "stdout": str,
            "stderr": str,
            "exit_code": int,
            "output_files": [{"name", "mime_type", "content_base64", ...}],
            "artifacts": [subset of output_files that are images/display],
            "action": "run_code",
            "thread_id": str,
            "session": {...},
        }
    """
    status = result.get("status", "error")
    success = status == "ok"
    stdout: str = result.get("stdout", "")
    stderr: str = result.get("stderr", "")
    exit_code: int = result.get("exit_code", 0 if success else 1)
    artifacts: list[dict[str, Any]] = result.get("artifacts", [])
    output_files: list[dict[str, Any]] = result.get("output_files", [])

    text_parts: list[str] = []
    media: list[ImageBlock] = []

    if stdout:
        text_parts.append(stdout.rstrip())
    if stderr:
        text_parts.append(f"[stderr] {stderr.rstrip()}")

    # Surface display artifacts (images / plots) in the response
    for artifact in artifacts:
        mime: str = artifact.get("mime_type", "")
        name: str = artifact.get("name", "artifact")
        content_b64: str = artifact.get("content_base64", "")
        if mime.startswith("image/") and content_b64:
            try:
                media.append(
                    ImageBlock(data=base64.b64decode(content_b64), media_type=mime)
                )
                text_parts.append(f"[Generated {name}]")
            except Exception:
                text_parts.append(f"[Generated {name}] (image decode failed)")
        elif content_b64:
            text_parts.append(f"[File: {name}] ({mime})")

    text = "\n".join(text_parts) if text_parts else "(no output)"

    response_data: dict[str, Any] = {
        "success": success,
        "output": text,
        "exit_code": exit_code,
    }

    # Include non-image file names so the agent knows what was written
    non_image_files = [
        {"name": f.get("name"), "mime_type": f.get("mime_type")}
        for f in output_files
        if not str(f.get("mime_type", "")).startswith("image/")
    ]
    if non_image_files:
        response_data["output_files"] = non_image_files

    return ToolExecutionResult(
        content=[TextBlock(text=json.dumps(response_data)), *media],
        is_error=not success,
    )


def sandbox_error_result(message: str) -> ToolExecutionResult:
    """A structured error result for transport-level failures (connection
    errors, timeouts) that never reach the sandbox_runtime.py response
    shape at all."""
    return ToolExecutionResult(
        content=[TextBlock(text=json.dumps({"success": False, "error": message}))],
        is_error=True,
    )
