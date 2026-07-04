"""K8sSandboxCodeInterpreterTool — BaseTool wrapper for kubernetes-sigs/agent-sandbox.

Each conversation thread gets its own Kubernetes pod created from a SandboxTemplate.
Python state (variables, installed packages, files) persists across calls within the
same session — exactly like Claude/OpenAI Code Interpreter.

This tool targets cluster deployments (Kind, EKS, GKE, etc.).
For local / VM isolation use CodeInterpreterTool (Firecracker-based).

Architecture::

    Agent ──► K8sSandboxCodeInterpreterTool ──► CodeInterpreterService (sync → thread)
                                                        │
                                                        ▼
                                          sandbox-router-svc (k8s)
                                                        │
                                                        ▼
                                          sandbox pod (SandboxTemplate)
                                          └── sandbox_runtime FastAPI :8888

Usage::

    # Minimal (uses env defaults)
    tool = K8sSandboxCodeInterpreterTool()

    # Explicit config
    tool = K8sSandboxCodeInterpreterTool(
        template="python-sandbox-template",
        namespace="default",
        warmpool="python-sandbox-template",
    )

    tool.session_id = thread_id                          # bind to conversation
    result = await tool.execute(code="import pandas as pd; print(pd.__version__)")
"""

from __future__ import annotations
from substrate.logger import setup_logging

import asyncio
import base64
import json
import os
from typing import Any

from substrate.kernel import ImageBlock  # was ImageContent, MediaContent
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel import TextBlock

from .sandbox_service import CodeInterpreterConfig, CodeInterpreterService
from .session_store import JsonSessionStore, SessionStore

logger = setup_logging()

_DEFAULT_SESSION = "default"


class K8sSandboxCodeInterpreterTool:
    """Execute Python in a persistent Kubernetes agent-sandbox pod.

    Uses the kubernetes-sigs/agent-sandbox controller. One pod per conversation
    thread; pods are created from a SandboxTemplate and optionally drawn from a
    warm pool to minimise cold-start latency.

    Attributes:
        session_id: Conversation thread ID used to route to the correct sandbox pod.
                    Set this to the active thread/session ID before calling execute().
    """

    risk: str = "critical"  # TODO: L4-hitl  # executes arbitrary code

    def __init__(
        self,
        template: str | None = None,
        namespace: str | None = None,
        *,
        service: CodeInterpreterService | None = None,
        session_store: SessionStore | None = None,
        session_store_path: str | None = None,
        request_timeout: int = 120,
        sandbox_ready_timeout: int = 180,
        server_port: int = 8888,
        shutdown_after_seconds: int | None = None,
        warmpool: str | None = None,
    ) -> None:
        self.name = "code_interpreter"
        self.description = (
            "Execute Python code in a secure, isolated Kubernetes sandbox pod. "
            "Python state persists between calls: variables defined in one call "
            "are available in the next. "
            "Available packages: numpy, pandas, matplotlib, scipy, scikit-learn, "
            "seaborn, plotly, openpyxl, polars, Pillow, requests. "
            "Matplotlib and Plotly figures are auto-captured and returned as artifacts. "
            "Print results via print() or return them from expressions."
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Use print() to produce visible output."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max execution time in seconds (default 60, max 300)",
                    "default": 60,
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        }

        if service is not None:
            self._service = service
        else:
            if session_store is not None and session_store_path is not None:
                raise ValueError(
                    "Pass either session_store or session_store_path, not both."
                )
            store: SessionStore | None = session_store
            if store is None and session_store_path is not None:
                store = JsonSessionStore(session_store_path)

            resolved_template = template or os.environ.get(
                "CI_SANDBOX_TEMPLATE", "python-sandbox-template"
            )
            resolved_namespace = namespace or os.environ.get(
                "CI_SANDBOX_NAMESPACE", "default"
            )

            config = CodeInterpreterConfig(
                template=resolved_template,
                namespace=resolved_namespace,
                request_timeout=request_timeout,
                sandbox_ready_timeout=sandbox_ready_timeout,
                server_port=server_port,
                shutdown_after_seconds=shutdown_after_seconds,
                warmpool=warmpool,
            )
            self._service = CodeInterpreterService(config=config, store=store)

        self.session_id: str = _DEFAULT_SESSION

    # ── Tool execution ──────────────────────────────────────────────────────────

    async def execute(
        self,
        *,
        ctx: RunMeta | None = None,
        code: str,
        timeout: int = 60,
        **_: Any,
    ) -> ToolExecutionResult:
        """Execute Python code in the session's sandbox pod.

        The underlying CodeInterpreterService is synchronous (blocking k8s API
        calls), so execution is offloaded to a thread via asyncio.to_thread().
        """
        timeout = max(1, min(timeout, 300))
        session_id = self.session_id

        logger.info(
            "k8s_sandbox[%s]: executing %d bytes (timeout=%ds)",
            session_id,
            len(code),
            timeout,
        )

        try:
            result: dict[str, Any] = await asyncio.to_thread(
                self._service.run_code,
                session_id,
                code,
                timeout,
            )
            if result.get("status") != "ok":
                raise RuntimeError(f"Sandbox run status is: {result.get('status')}")
            return self._to_tool_result(result)
        except Exception as exc:
            logger.warning(
                "k8s_sandbox[%s] failed or is unavailable. Activating local fallback sandbox. Reason: %s",
                session_id,
                exc,
            )
            from ..tool import CodeInterpreterTool

            local_tool = CodeInterpreterTool()
            local_tool.session_id = self.session_id
            return await local_tool.execute(code=code, timeout=timeout)

    # ── Response conversion ─────────────────────────────────────────────────────

    def _to_tool_result(self, result: dict[str, Any]) -> ToolExecutionResult:
        """Convert CodeInterpreterService response dict → ToolResult.

        The sandbox runtime (/ci/run) returns::

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
                        ImageBlock(
                            data=base64.b64decode(content_b64),
                            media_type=mime,
                        )
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
