"""CodeInterpreterTool — session-aware BaseTool using HTTP client.

Each agent session (conversation thread) gets its own persistent
Firecracker microVM on the code-interpreter service pod.  Variables,
files, and installed packages survive between calls within the same
session — exactly like Claude/OpenAI Code Interpreter.

Architecture::

    Agent ──► CodeInterpreterTool ──► CodeInterpreterClient (HTTP)
                                          │
                                          ▼
                              code-interpreter-{N} pod
                              (StatefulSet, privileged)
                                          │
                                          ▼
                                  SessionManager → VM

Usage::

    # HTTP mode (production / k8s)
    client = CodeInterpreterClient(base_url="http://code-interpreter:8080")
    tool = CodeInterpreterTool(http_client=client)

    # Direct mode (local dev / testing)
    tool = CodeInterpreterTool(session_manager=sm)

    tool.session_id = thread_id
    result = await tool.execute(code="x = 42")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from agent_framework.tools.base_tool import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_SESSION = "default"


class CodeInterpreterTool(BaseTool):
    """Execute Python / bash in a persistent Firecracker microVM session.

    Supports two modes:
      - **HTTP mode**: routes to the code-interpreter service via HTTP
      - **Direct mode**: uses a local SessionManager (testing only)
    """

    def __init__(
        self,
        http_client: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        # Legacy compat
        config: Optional[Any] = None,
        pool: Optional[Any] = None,
    ):
        super().__init__(
            name="code_interpreter",
            description=(
                "Execute Python or bash code in a secure, isolated microVM. "
                "Python state persists between calls: variables you define in "
                "one call are available in the next. "
                "Use exec_type='bash' for shell commands (ls, cat, curl, etc.). "
                "Available packages: numpy, pandas, matplotlib, scipy, sympy, requests. "
                "Matplotlib figures are auto-captured and returned as images. "
                "Print results via print() or return them from expressions."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python code to execute (exec_type='python') "
                            "or a bash command (exec_type='bash'). "
                            "Use print() to show output."
                        ),
                    },
                    "exec_type": {
                        "type": "string",
                        "enum": ["python", "bash"],
                        "description": "'python' (default) or 'bash'",
                        "default": "python",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max execution time in seconds (default 30, max 300)",
                        "default": 30,
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        )

        self._http_client = http_client
        self._session_manager = session_manager
        self._mode: str = "none"

        if http_client:
            self._mode = "http"
        elif session_manager:
            self._mode = "direct"
        elif config or pool:
            # Legacy: build a session manager locally
            self._mode = "direct"
            self._deferred_config = config
            self._deferred_pool = pool
        else:
            # Auto-detect from env
            url = os.environ.get("CODE_INTERPRETER_URL", "")
            if url:
                from .http_client import CodeInterpreterClient
                self._http_client = CodeInterpreterClient(
                    base_url=url,
                    auth_token=os.environ.get("CI_AUTH_TOKEN", ""),
                    replicas=int(os.environ.get("CI_REPLICAS", "1")),
                    headless_service=os.environ.get("CI_HEADLESS_SERVICE", ""),
                    namespace=os.environ.get("CI_NAMESPACE", "agent-framework"),
                )
                self._mode = "http"

        self.session_id: str = _DEFAULT_SESSION

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start local session manager (direct mode only)."""
        if self._mode == "direct" and self._session_manager is None:
            from .config import CodeInterpreterConfig
            from .session_manager import SessionManager
            from .vm_manager import VMPool

            cfg = getattr(self, "_deferred_config", None) or CodeInterpreterConfig()
            pool = getattr(self, "_deferred_pool", None) or VMPool(cfg)
            self._session_manager = SessionManager(cfg, pool)
            await self._session_manager.start()

    async def stop(self) -> None:
        """Shut down local session manager or close HTTP client."""
        if self._mode == "direct" and self._session_manager:
            await self._session_manager.stop()
        elif self._mode == "http" and self._http_client:
            await self._http_client.close()

    # ── Tool execution ────────────────────────────────────────────────────────

    async def execute(
        self,
        code: str,
        exec_type: str = "python",
        timeout: int = 30,
        **kwargs: Any,
    ) -> ToolResult:
        """Execute code in the current session's VM."""
        timeout = max(1, min(timeout, 300))

        logger.info(
            "code_interpreter[%s]: %s %d bytes (timeout=%ds)",
            self.session_id, exec_type, len(code), timeout,
        )

        if self._mode == "http":
            return await self._execute_http(code, exec_type, timeout)
        elif self._mode == "direct":
            return await self._execute_direct(code, exec_type, timeout)
        else:
            return ToolResult(
                content=[{"type": "text", "text": json.dumps({
                    "success": False,
                    "error": (
                        "Code interpreter not configured. "
                        "Set CODE_INTERPRETER_URL env var or provide http_client."
                    ),
                })}],
                isError=True,
            )

    # ── HTTP mode ─────────────────────────────────────────────────────────────

    async def _execute_http(self, code: str, exec_type: str, timeout: int) -> ToolResult:
        """Execute via the code-interpreter HTTP service."""
        try:
            resp = await self._http_client.execute(
                session_id=self.session_id,
                code=code,
                exec_type=exec_type,
                timeout=timeout,
            )
        except Exception as exc:
            logger.error("code_interpreter HTTP error: %s", exc, exc_info=True)
            return ToolResult(
                content=[{"type": "text", "text": json.dumps({
                    "success": False, "error": f"{type(exc).__name__}: {exc}",
                })}],
                isError=True,
            )

        return self._response_to_tool_result(resp)

    def _response_to_tool_result(self, resp) -> ToolResult:
        """Convert ExecuteResponse → ToolResult with multimodal content."""
        # Build text summary for the LLM
        text_parts = []
        images = []

        for output in resp.outputs:
            if output.type.value == "text":
                text_parts.append(output.content.rstrip())
            elif output.type.value == "stderr":
                text_parts.append(f"[stderr] {output.content.rstrip()}")
            elif output.type.value == "error":
                text_parts.append(f"[error] {output.content.rstrip()}")
            elif output.type.value == "image":
                images.append({
                    "name": output.name or "figure.png",
                    "format": output.format or "png",
                    "data": output.content,  # base64
                })
                text_parts.append(f"[Generated {output.name or 'figure.png'}]")
            elif output.type.value == "file":
                text_parts.append(
                    f"[File: {output.name or 'output'}] "
                    f"({output.format or 'binary'}, {len(output.content)} bytes)"
                )

        text = "\n".join(text_parts) if text_parts else "(no output)"

        # Build the full response JSON (frontend can parse for images)
        response_data = {
            "success": resp.success,
            "output": text,
            "execution_time": resp.execution_time,
            "cell_id": resp.cell_id,
            "exec_type": "python",
        }
        if images:
            response_data["images"] = images
        if resp.error:
            response_data["error"] = resp.error

        return ToolResult(
            content=[{"type": "text", "text": json.dumps(response_data)}],
            isError=not resp.success,
        )

    # ── Direct mode ───────────────────────────────────────────────────────────

    async def _execute_direct(self, code: str, exec_type: str, timeout: int) -> ToolResult:
        """Execute via local SessionManager (testing / local dev)."""
        if self._session_manager is None:
            await self.start()

        if exec_type == "bash":
            request = {"type": "bash", "cmd": code, "timeout": timeout}
        else:
            request = {"type": "python", "code": code, "timeout": timeout}

        try:
            result = await self._session_manager.execute(self.session_id, request)
        except Exception as exc:
            logger.error("code_interpreter direct error: %s", exc, exc_info=True)
            return ToolResult(
                content=[{"type": "text", "text": json.dumps({
                    "success": False, "error": f"{type(exc).__name__}: {exc}",
                })}],
                isError=True,
            )

        return self._build_direct_result(result, exec_type)

    def _build_direct_result(self, result: dict, exec_type: str) -> ToolResult:
        """Convert raw guest-agent dict → ToolResult (direct mode)."""
        success = result.get("success", False)

        # Handle v3 structured outputs
        if "outputs" in result and result["outputs"]:
            text_parts = []
            images = []
            for o in result["outputs"]:
                otype = o.get("type", "text")
                if otype == "text":
                    text_parts.append(o["content"].rstrip())
                elif otype == "stderr":
                    text_parts.append(f"[stderr] {o['content'].rstrip()}")
                elif otype == "error":
                    text_parts.append(f"[error] {o['content'].rstrip()}")
                elif otype == "image":
                    images.append({
                        "name": o.get("name", "figure.png"),
                        "format": o.get("format", "png"),
                        "data": o["content"],
                    })
                    text_parts.append(f"[Generated {o.get('name', 'figure.png')}]")

            text = "\n".join(text_parts) if text_parts else "(no output)"
            data = {
                "success": success, "output": text,
                "execution_time": result.get("execution_time", 0),
                "cell_id": result.get("cell_id"),
                "exec_type": exec_type,
            }
            if images:
                data["images"] = images
            if result.get("error"):
                data["error"] = result["error"]

            return ToolResult(
                content=[{"type": "text", "text": json.dumps(data)}],
                isError=not success,
            )

        # v2 fallback
        if success:
            parts = []
            if result.get("output"):
                parts.append(result["output"].rstrip())
            if result.get("stderr"):
                parts.append(f"[stderr]\n{result['stderr'].rstrip()}")
            text = "\n".join(parts) if parts else "(no output)"

            return ToolResult(
                content=[{"type": "text", "text": json.dumps({
                    "success": True, "output": text,
                    "execution_time": result.get("execution_time", 0),
                    "cell_id": result.get("cell_id"), "exec_type": exec_type,
                })}],
                isError=False,
            )
        else:
            return ToolResult(
                content=[{"type": "text", "text": json.dumps({
                    "success": False, "error": result.get("error", "Unknown error"),
                    "output": result.get("output", ""),
                    "stderr": result.get("stderr", ""),
                    "exec_type": exec_type,
                })}],
                isError=True,
            )
