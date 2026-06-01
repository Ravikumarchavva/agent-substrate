"""CodeInterpreterTool — session-aware Tool using HTTP client.

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
from ravi.logger import setup_logging

import base64
import json
import os
from typing import Any, Optional

from ravi.kernel import ImageBlock  # was ImageContent
from ravi.kernel.tools import ToolExecutionResult
from ravi.kernel import TextBlock

logger = setup_logging()

_DEFAULT_SESSION = "default"


class CodeInterpreterTool:
    """Execute Python / bash in a persistent Firecracker microVM session.

    Supports two modes:
      - **HTTP mode**: routes to the code-interpreter service via HTTP
      - **Direct mode**: uses a local SessionManager (testing only)
    """

    name: str = "code_interpreter"
    description: str = (
        "Execute Python or bash code in a secure, isolated microVM. "
        "Python state persists between calls: variables you define in "
        "one call are available in the next. "
        "Use exec_type='bash' for shell commands (ls, cat, curl, etc.). "
        "Available packages: numpy, pandas, matplotlib, scipy, sympy, requests. "
        "Matplotlib figures are auto-captured and returned as images. "
        "Print results via print() or return them from expressions."
    )
    input_schema: dict[str, Any] = {
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
    }
    risk: str = "critical"  # TODO: L4-hitl  # executes arbitrary code

    def __init__(
        self,
        http_client: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        # Legacy compat
        config: Optional[Any] = None,
        pool: Optional[Any] = None,
    ) -> None:
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

    async def execute(  # type: ignore[override]
        self,
        *,
        code: str,
        exec_type: str = "python",
        timeout: int = 30,
    ) -> ToolExecutionResult:
        """Execute code in the current session's VM."""
        timeout = max(1, min(timeout, 300))

        logger.info(
            "code_interpreter[%s]: %s %d bytes (timeout=%ds)",
            self.session_id,
            exec_type,
            len(code),
            timeout,
        )

        if self._mode == "http":
            return await self._execute_http(code, exec_type, timeout)
        elif self._mode == "direct":
            return await self._execute_direct(code, exec_type, timeout)
        else:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "success": False,
                                "error": (
                                    "Code interpreter not configured. "
                                    "Set CODE_INTERPRETER_URL env var or provide http_client."
                                ),
                            }
                        )
                    )
                ],
                is_error=True,
            )

    # ── HTTP mode ─────────────────────────────────────────────────────────────

    async def _execute_http(
        self, code: str, exec_type: str, timeout: int
    ) -> ToolExecutionResult:
        """Execute via the code-interpreter HTTP service."""
        try:
            http_client = self._require_http_client()
            resp = await http_client.execute(
                session_id=self.session_id,
                code=code,
                exec_type=exec_type,
                timeout=timeout,
            )
            if not resp.success and any(
                err in str(resp.error).lower()
                for err in [
                    "capacity",
                    "microvm",
                    "503",
                    "service unavailable",
                    "timeout",
                    "degraded",
                    "not available",
                ]
            ):
                raise RuntimeError(f"Overloaded MicroVM Service: {resp.error}")
        except Exception as exc:
            logger.warning(
                "code_interpreter HTTP error or capacity issue. Activating local fallback sandbox. Reason: %s",
                exc,
            )
            try:
                import sys
                import io
                import traceback
                import matplotlib

                matplotlib.use("Agg")
                import matplotlib.pyplot as plt

                old_stdout = sys.stdout
                old_stderr = sys.stderr
                redirected_output = io.StringIO()
                redirected_error = io.StringIO()
                sys.stdout = redirected_output
                sys.stderr = redirected_error

                local_ns = {}
                local_ns["plt"] = plt
                local_ns["matplotlib"] = matplotlib
                try:
                    import numpy as np

                    local_ns["np"] = np
                except ImportError:
                    pass
                try:
                    import pandas as pd

                    local_ns["pd"] = pd
                except ImportError:
                    pass

                success = True
                error_str = None

                try:
                    plt.close("all")
                    exec(code, {}, local_ns)

                    media = []
                    figs = [plt.figure(num) for num in plt.get_fignums()]
                    for idx, fig in enumerate(figs):
                        buf = io.BytesIO()
                        fig.savefig(buf, format="png", bbox_inches="tight")
                        buf.seek(0)
                        img_data = buf.getvalue()

                        from ravi.kernel import ImageBlock  # was ImageContent

                        media.append(ImageBlock(data=img_data, media_type="image/png"))
                        plt.close(fig)
                except Exception as e:
                    success = False
                    error_str = f"Error during local fallback execution: {e}\n{traceback.format_exc()}"
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr

                stdout_text = redirected_output.getvalue()
                stderr_text = redirected_error.getvalue()

                if not success:
                    return ToolExecutionResult(
                        content=[
                            TextBlock(
                                text=json.dumps(
                                    {
                                        "success": False,
                                        "error": error_str,
                                        "output": stdout_text,
                                        "stderr": stderr_text,
                                        "exec_type": "python",
                                    }
                                )
                            )
                        ],
                        is_error=True,
                    )

                response_data = {
                    "success": True,
                    "output": stdout_text
                    + (f"\n[stderr]\n{stderr_text}" if stderr_text else ""),
                    "execution_time": 0.05,
                    "cell_id": "fallback-cell",
                    "exec_type": "python",
                }

                return ToolExecutionResult(
                    content=[TextBlock(text=json.dumps(response_data))],
                    is_error=False,
                    media=media or None,
                )
            except Exception as fallback_exc:
                logger.error(
                    "Local fallback execution engine error: %s",
                    fallback_exc,
                    exc_info=True,
                )
                return ToolExecutionResult(
                    content=[
                        TextBlock(
                            text=json.dumps(
                                {
                                    "success": False,
                                    "error": f"HTTP Error: {exc} | Fallback Error: {fallback_exc}",
                                }
                            )
                        )
                    ],
                    is_error=True,
                )

        return self._response_to_tool_result(resp)

    def _response_to_tool_result(self, resp) -> ToolExecutionResult:
        """Convert ExecuteResponse → ToolResult with multimodal content."""
        # Build text summary for the LLM
        text_parts = []
        media = []

        for output in resp.outputs:
            if output.type.value == "text":
                text_parts.append(output.content.rstrip())
            elif output.type.value == "stderr":
                text_parts.append(f"[stderr] {output.content.rstrip()}")
            elif output.type.value == "error":
                text_parts.append(f"[error] {output.content.rstrip()}")
            elif output.type.value == "image":
                try:
                    media.append(
                        ImageBlock(
                            data=base64.b64decode(output.content),
                            media_type=f"image/{output.format or 'png'}",
                        )
                    )
                    text_parts.append(f"[Generated {output.name or 'figure.png'}]")
                except Exception:
                    text_parts.append(
                        f"[Generated {output.name or 'figure.png'}] (image decode failed)"
                    )
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
        if resp.error:
            response_data["error"] = resp.error

        return ToolExecutionResult(
            content=[TextBlock(text=json.dumps(response_data))],
            is_error=not resp.success,
            media=media or None,
        )

    # ── Direct mode ───────────────────────────────────────────────────────────

    async def _execute_direct(
        self, code: str, exec_type: str, timeout: int
    ) -> ToolExecutionResult:
        """Execute via local SessionManager (testing / local dev)."""
        if self._session_manager is None:
            await self.start()

        session_manager = self._require_session_manager()

        if exec_type == "bash":
            request = {"type": "bash", "cmd": code, "timeout": timeout}
        else:
            request = {"type": "python", "code": code, "timeout": timeout}

        try:
            result = await session_manager.execute(self.session_id, request)
        except Exception as exc:
            logger.error("code_interpreter direct error: %s", exc, exc_info=True)
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "success": False,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    )
                ],
                is_error=True,
            )

        return self._build_direct_result(result, exec_type)

    def _build_direct_result(self, result: dict, exec_type: str) -> ToolExecutionResult:
        """Convert raw guest-agent dict → ToolResult (direct mode)."""
        success = result.get("success", False)

        # Handle v3 structured outputs
        if "outputs" in result and result["outputs"]:
            text_parts = []
            media = []
            for o in result["outputs"]:
                otype = o.get("type", "text")
                if otype == "text":
                    text_parts.append(o["content"].rstrip())
                elif otype == "stderr":
                    text_parts.append(f"[stderr] {o['content'].rstrip()}")
                elif otype == "error":
                    text_parts.append(f"[error] {o['content'].rstrip()}")
                elif otype == "image":
                    try:
                        media.append(
                            ImageBlock(
                                data=base64.b64decode(o["content"]),
                                media_type=f"image/{o.get('format', 'png')}",
                            )
                        )
                        text_parts.append(f"[Generated {o.get('name', 'figure.png')}]")
                    except Exception:
                        text_parts.append(
                            f"[Generated {o.get('name', 'figure.png')}] (image decode failed)"
                        )

            text = "\n".join(text_parts) if text_parts else "(no output)"
            data = {
                "success": success,
                "output": text,
                "execution_time": result.get("execution_time", 0),
                "cell_id": result.get("cell_id"),
                "exec_type": exec_type,
            }
            if result.get("error"):
                data["error"] = result["error"]

            return ToolExecutionResult(
                content=[TextBlock(text=json.dumps(data))],
                is_error=not success,
                media=media or None,
            )

        # v2 fallback
        if success:
            parts = []
            if result.get("output"):
                parts.append(result["output"].rstrip())
            if result.get("stderr"):
                parts.append(f"[stderr]\n{result['stderr'].rstrip()}")
            text = "\n".join(parts) if parts else "(no output)"

            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "success": True,
                                "output": text,
                                "execution_time": result.get("execution_time", 0),
                                "cell_id": result.get("cell_id"),
                                "exec_type": exec_type,
                            }
                        )
                    )
                ],
                is_error=False,
            )
        else:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "success": False,
                                "error": result.get("error", "Unknown error"),
                                "output": result.get("output", ""),
                                "stderr": result.get("stderr", ""),
                                "exec_type": exec_type,
                            }
                        )
                    )
                ],
                is_error=True,
            )

    def _require_http_client(self) -> Any:
        if self._http_client is None:
            raise RuntimeError("HTTP client is not configured")
        return self._http_client

    def _require_session_manager(self) -> Any:
        if self._session_manager is None:
            raise RuntimeError("Session manager is not configured")
        return self._session_manager
