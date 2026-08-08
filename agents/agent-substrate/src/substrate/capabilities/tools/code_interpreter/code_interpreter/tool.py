"""CodeInterpreterTool — the single LLM-facing code-execution tool.

Replaces the previous pair of near-duplicate tools (one per deployment target)
with one tool plus an injected :class:`SandboxRuntime`. The agent never learns
which backend it is talking to, so swapping bubblewrap → k8s/gVisor →
Firecracker is a wiring change, not a tool change.

Two execution modes, both isolated identically by the runtime:
  * ``code``    — Python source (the common path)
  * ``command`` — a shell command line (``ls -la``, ``wc -l data.csv``, …)

The working directory is always the caller's own session directory
(``users/{uid}/sessions/{tid}``), which the runtime makes the *only* visible
part of the workspace. Files written there persist and are addressable from
chat via the ``sandbox:`` scheme.
"""

from __future__ import annotations

import shlex
from typing import Any

from substrate.agents.storage.tasks import current_thread_id, current_user_id
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel.tools.tools import ToolRisk
from substrate.logger import setup_logging

from .code_risk import classify_and_summarize
from .runtimes.base import NetworkPolicy, SandboxRuntime, SandboxSpec
from .sandbox_response import (
    PRESENTATION_GUIDANCE,
    sandbox_error_result,
    sandbox_result_to_tool_result,
)

logger = setup_logging()

_DEFAULT_SESSION = "default"
_MAX_TIMEOUT = 300


class CodeInterpreterTool:
    """Execute Python or shell in an isolated, session-scoped sandbox."""

    risk: str = "critical"  # executes arbitrary code

    def __init__(
        self,
        runtime: SandboxRuntime,
        *,
        network: NetworkPolicy = NetworkPolicy.DENY,
        default_timeout_s: int = 60,
        memory_bytes: int = 2 * 1024 * 1024 * 1024,
        model_client: Any | None = None,
    ) -> None:
        self._runtime = runtime
        self._network = network
        self._default_timeout_s = default_timeout_s
        self._memory_bytes = memory_bytes
        # Only used to summarize dangerous code for the approval card.
        self._model_client = model_client
        # Set by chain callers that have no ContextVar in scope.
        self.session_id: str = _DEFAULT_SESSION

        self.name = "code_interpreter"
        self.description = (
            "Execute Python code or a shell command in a secure, isolated sandbox. "
            "Your working directory is this conversation's own workspace: files you "
            "read and write there persist across turns, and no other user's files "
            "are visible. Available packages: numpy, pandas, matplotlib, scipy, "
            "scikit-learn, seaborn, plotly, openpyxl, polars, Pillow, requests, "
            "python-docx, python-pptx, reportlab, pdfplumber. "
            "Each execution runs in a fresh interpreter, so Python variables do NOT "
            "persist between calls — save anything you need later to a file. "
            "Network access is disabled by default." + PRESENTATION_GUIDANCE
        )
        self.input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Use print() to produce visible "
                        "output. Mutually exclusive with 'command'."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to run instead of Python, e.g. 'ls -la' or "
                        "'wc -l data.csv'. Mutually exclusive with 'code'."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Max execution time in seconds (default 60, max 300)",
                    "default": 60,
                },
            },
            "additionalProperties": False,
        }

    async def classify_risk(
        self, arguments: dict[str, Any]
    ) -> tuple[ToolRisk, str | None]:
        """Per-call risk, consulted by ToolInvoker's approval gate. Exploratory
        work is SAFE; shell/network/deletion/out-of-workspace writes are CRITICAL
        and gate with a human-readable summary."""
        payload = str(arguments.get("code") or "")
        command = str(arguments.get("command") or "")
        if command:
            # Route the command through the same static classifier by framing it
            # the way the AST rules already recognise as a shell invocation.
            payload = f"import subprocess; subprocess.run({shlex.split(command)!r})"
        return await classify_and_summarize(payload, self._model_client)

    async def execute(
        self,
        *,
        ctx: RunMeta | None = None,
        code: str | None = None,
        command: str | None = None,
        timeout: int = 60,
        **_: Any,
    ) -> ToolExecutionResult:
        if bool(code) == bool(command):
            return sandbox_error_result(
                "Provide exactly one of 'code' (Python) or 'command' (shell)."
            )

        timeout_s = max(1, min(int(timeout or self._default_timeout_s), _MAX_TIMEOUT))
        thread_id = current_thread_id.get()
        session_id = (
            thread_id
            if thread_id and thread_id != _DEFAULT_SESSION
            else self.session_id
        )
        user_id = current_user_id.get()

        spec = SandboxSpec(
            user_id=user_id,
            thread_id=session_id,
            session_dir=session_dir(user_id, session_id),
            code=code or None,
            argv=shlex.split(command) if command else None,
            timeout_s=timeout_s,
            network=self._network,
            memory_bytes=self._memory_bytes,
        )

        logger.info(
            "code_interpreter[%s/%s]: %s via %s (timeout=%ds)",
            user_id or "anon",
            session_id,
            "command" if command else f"{len(code or '')} bytes of python",
            self._runtime.name,
            timeout_s,
        )

        try:
            result = await self._runtime.execute(spec)
        except Exception as exc:  # noqa: BLE001 - report any backend failure
            logger.error("code_interpreter[%s] runtime error: %s", session_id, exc)
            return sandbox_error_result(f"Sandbox error: {exc}")

        return sandbox_result_to_tool_result(result.to_sandbox_response())

    async def stop(self) -> None:
        """Release backend resources. Picked up automatically by the monolith's
        shutdown loop, which duck-types ``hasattr(tool, "stop")``."""
        await self._runtime.stop()


def session_dir(user_id: str | None, thread_id: str) -> str:
    """The store-relative session key the runtime scopes execution to.

    Mirrors ``routes/workspace.py::_session_key`` so the sandbox writes exactly
    where the file-serving endpoint, ``sandbox:`` refs, and versioning read.
    Without a user identity there is no per-user tree to scope into, so fall
    back to a session-only path. Public (not `_session_dir`) because
    `SkillTool` also needs this exact key to stage a skill's `scripts/` files
    into the same session before the sandbox runs — see
    `skills/tool.py::_activate`.
    """
    if user_id:
        return f"users/{user_id}/sessions/{thread_id}"
    return f"sessions/{thread_id}"


__all__ = ["CodeInterpreterTool", "session_dir"]
