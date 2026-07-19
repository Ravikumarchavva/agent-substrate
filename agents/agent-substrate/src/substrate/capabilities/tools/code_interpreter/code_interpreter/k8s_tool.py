"""K8sSandboxCodeInterpreterTool — BaseTool wrapper for kubernetes-sigs/agent-sandbox.

Each conversation thread gets its own Kubernetes pod created from a SandboxTemplate.
Python state (variables, installed packages, files) persists across calls within the
same session — exactly like Claude/OpenAI Code Interpreter.

This tool targets cluster deployments (Kind, EKS, GKE, etc.).
For local dev use LocalSandboxCodeInterpreterTool, which talks to the same
sandbox_runtime.py server directly over HTTP (no Kubernetes required).

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
import os
from typing import Any

from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.tools import ToolExecutionResult
from substrate.agents.storage.tasks import current_thread_id, current_user_id
from substrate.kernel.tools.tools import ToolRisk

from .code_risk import classify_and_summarize
from .sandbox_response import (
    PRESENTATION_GUIDANCE,
    sandbox_error_result,
    sandbox_result_to_tool_result,
)
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

    risk: str = "critical"  # executes arbitrary code

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
        workspace_pvc_claim: str | None = None,
        workspace_mount_path: str = "/app/workspace",
        local_sandbox_url: str | None = None,
        model_client: Any | None = None,
    ) -> None:
        # Optional LLM client used only to summarize dangerous code for the
        # approval card (hybrid classifier — see classify_risk).
        self._model_client = model_client
        # Explicit param takes precedence — os.environ.get() alone would miss
        # values pydantic-settings loaded from .env (that loading populates
        # ServerSettings, not the process environment), so the monolith must
        # pass this through from cfg.CI_LOCAL_SANDBOX_URL (see
        # infrastructure/serving_factory.py). Falling back to os.environ
        # still covers callers that set it as a real env var (e.g. the
        # tool_executor microservice).
        self._local_sandbox_url = local_sandbox_url or os.environ.get(
            "CI_LOCAL_SANDBOX_URL", ""
        )
        self.name = "code_interpreter"
        self.description = (
            "Execute Python code in a secure, isolated Kubernetes sandbox pod. "
            "Python state persists between calls: variables defined in one call "
            "are available in the next. "
            "Available packages: numpy, pandas, matplotlib, scipy, scikit-learn, "
            "seaborn, plotly, openpyxl, polars, Pillow, requests. "
            "Print results via print() or return them from expressions."
            + PRESENTATION_GUIDANCE
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
            resolved_pvc_claim = workspace_pvc_claim or os.environ.get(
                "CI_WORKSPACE_PVC_CLAIM"
            )
            resolved_mount_path = workspace_mount_path or os.environ.get(
                "CI_WORKSPACE_MOUNT_PATH", "/app/workspace"
            )

            config = CodeInterpreterConfig(
                template=resolved_template,
                namespace=resolved_namespace,
                request_timeout=request_timeout,
                sandbox_ready_timeout=sandbox_ready_timeout,
                server_port=server_port,
                shutdown_after_seconds=shutdown_after_seconds,
                warmpool=warmpool,
                workspace_pvc_claim=resolved_pvc_claim,
                workspace_mount_path=resolved_mount_path,
            )
            self._service = CodeInterpreterService(config=config, store=store)

        self.session_id: str = _DEFAULT_SESSION

    async def classify_risk(
        self, arguments: dict[str, Any]
    ) -> tuple[ToolRisk, str | None]:
        """Per-call risk: exploratory code is SAFE (no approval); dangerous
        code is CRITICAL and gates with a summary. Consulted by ToolInvoker's
        risk gate (see local_sandbox_tool.py for the shared rationale)."""
        code = str(arguments.get("code", ""))
        return await classify_and_summarize(code, self._model_client)

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
        # Prefer the active thread (stamped as a ContextVar inside ReActAgent's
        # Worker task) so the sandbox pod's run cwd is sessions/{thread_id}
        # under the per-user subPath mount — where uploads for this thread
        # live and where the workspace file-serve endpoint resolves. Falls
        # back to whatever was set on the instance (chain calls set it).
        thread_id = current_thread_id.get()
        session_id = (
            thread_id if thread_id and thread_id != "default" else self.session_id
        )
        user_id = current_user_id.get()

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
                user_id,
            )
            if result.get("status") != "ok":
                raise RuntimeError(f"Sandbox run status is: {result.get('status')}")
            return sandbox_result_to_tool_result(result)
        except Exception as exc:
            local_sandbox_url = self._local_sandbox_url
            if not local_sandbox_url:
                logger.error(
                    "k8s_sandbox[%s] failed and no CI_LOCAL_SANDBOX_URL fallback is configured: %s",
                    session_id,
                    exc,
                )
                return sandbox_error_result(f"Sandbox unavailable: {exc}")
            logger.warning(
                "k8s_sandbox[%s] failed or is unavailable. Activating local sandbox fallback. Reason: %s",
                session_id,
                exc,
            )
            from .local_sandbox_tool import LocalSandboxCodeInterpreterTool

            local_tool = LocalSandboxCodeInterpreterTool(base_url=local_sandbox_url)
            local_tool.session_id = self.session_id
            return await local_tool.execute(code=code, timeout=timeout)
