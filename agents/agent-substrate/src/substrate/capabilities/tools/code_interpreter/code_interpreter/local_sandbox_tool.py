"""LocalSandboxCodeInterpreterTool — talks directly (plain HTTP, no Kubernetes)
to a locally-composed sandbox_runtime.py container.

Same wire protocol and container image as K8sSandboxCodeInterpreterTool
(code_interpreter/agent-sandbox/Dockerfile, sandbox_runtime.py's /ci/run REST
API) — this is the local-dev equivalent that skips the k8s_agent_sandbox
SandboxClient/CRD/tunnel machinery entirely, since in local dev the sandbox
container is just a docker-compose service reachable directly over HTTP (see
deployment/docker/docker-compose.yml, ``--profile sandbox``) with the local
workspace directory bind-mounted in, so uploaded chat attachments are visible
to executed code without any VM/PVC plumbing.

Usage::

    tool = LocalSandboxCodeInterpreterTool(base_url="http://localhost:8023")
    tool.session_id = thread_id
    result = await tool.execute(code="import pandas as pd; print(pd.__version__)")
"""

from __future__ import annotations
from substrate.logger import setup_logging

from typing import Any

import httpx

from substrate.agents.storage.tasks import current_thread_id, current_user_id
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel.tools.tools import ToolRisk

from .code_risk import classify_and_summarize
from .sandbox_response import (
    PRESENTATION_GUIDANCE,
    sandbox_error_result,
    sandbox_result_to_tool_result,
)

logger = setup_logging()

_DEFAULT_SESSION = "default"


def _run_dir_for_call() -> tuple[str, str | None]:
    """Return (session_id, workspace_dir) for the /ci/run call.

    The local sandbox mounts the WHOLE workspace, so when a user + thread are
    in scope (stamped as ContextVars inside ReActAgent's Worker task) we point
    the run cwd at the caller's own per-thread directory —
    ``users/{uid}/sessions/{tid}`` — landing agent-written files next to the
    user's uploads and under an ownership-scoped path the workspace
    file-serve endpoint can resolve. Absent that context, fall back to the
    plain session_id behavior.
    """
    thread_id = current_thread_id.get()
    user_id = current_user_id.get()
    if user_id and thread_id and thread_id != "default":
        return thread_id, f"users/{user_id}/sessions/{thread_id}"
    return thread_id or _DEFAULT_SESSION, None


class LocalSandboxCodeInterpreterTool:
    """Execute Python in a locally-composed sandbox container (no Kubernetes)."""

    risk: str = "critical"  # executes arbitrary code

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str = "",
        timeout_s: float = 120.0,
        model_client: Any | None = None,
    ) -> None:
        # Optional LLM client used only to summarize dangerous code for the
        # approval card (hybrid classifier — see classify_risk).
        self._model_client = model_client
        self.name = "code_interpreter"
        self.description = (
            "Execute Python code in a secure, isolated sandbox container. "
            "Python state persists between calls: variables defined in one call "
            "are available in the next. "
            "Available packages: numpy, pandas, matplotlib, scipy, scikit-learn, "
            "seaborn, plotly, openpyxl, polars, Pillow, requests. "
            "Print results via print() or return them from expressions."
            + PRESENTATION_GUIDANCE
        )
        self.input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use print() to produce visible output.",
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

        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None
        self.session_id: str = _DEFAULT_SESSION

    async def classify_risk(
        self, arguments: dict[str, Any]
    ) -> tuple[ToolRisk, str | None]:
        """Per-call risk: exploratory code (pandas/matplotlib/reads/workspace
        saves) is SAFE and runs without approval; dangerous code (shell,
        network, deletion, out-of-workspace writes, dynamic exec) is CRITICAL
        and gates with a summary. Consulted by ToolInvoker's risk gate."""
        code = str(arguments.get("code", ""))
        return await classify_and_summarize(code, self._model_client)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=5.0, read=self._timeout_s, write=10.0, pool=5.0
                ),
                headers=self._headers,
            )
        return self._client

    async def execute(
        self,
        *,
        ctx: RunMeta | None = None,
        code: str,
        timeout: int = 60,
        **_: Any,
    ) -> ToolExecutionResult:
        """Execute code against the sandbox container's /ci/run endpoint."""
        timeout = max(1, min(timeout, 300))
        client = self._get_client()
        session_id, workspace_dir = _run_dir_for_call()

        logger.info(
            "local_sandbox[%s]: executing %d bytes (timeout=%ds)",
            session_id,
            len(code),
            timeout,
        )

        try:
            payload: dict[str, Any] = {"code": code, "session_id": session_id}
            if workspace_dir is not None:
                payload["workspace_dir"] = workspace_dir
            resp = await client.post(
                "/ci/run",
                json=payload,
                timeout=httpx.Timeout(
                    connect=5.0, read=timeout + 10, write=10.0, pool=5.0
                ),
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "local_sandbox[%s] HTTP %d: %s",
                session_id,
                exc.response.status_code,
                exc.response.text[:500],
            )
            return sandbox_error_result(
                f"Sandbox error {exc.response.status_code}: {exc.response.text[:200]}"
            )
        except httpx.TimeoutException:
            return sandbox_error_result(f"Sandbox did not respond within {timeout}s")
        except httpx.RequestError as exc:
            logger.error("local_sandbox[%s] connection error: %s", session_id, exc)
            return sandbox_error_result(f"Sandbox connection error: {exc}")

        return sandbox_result_to_tool_result(result)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
