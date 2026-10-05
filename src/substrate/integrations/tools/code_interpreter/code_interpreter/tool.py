"""CodeInterpreterTool — the single LLM-facing code-execution tool.

Replaces the previous pair of near-duplicate tools (one per deployment target)
with one tool plus an injected :class:`SandboxRuntime`. The agent never learns
which backend it is talking to, so swapping nsjail → k8s/gVisor →
Firecracker is a wiring change, not a tool change.

Two execution modes, both isolated identically by the runtime:
  * ``code``    — Python source (the common path)
  * ``command`` — a shell command line (``ls -la``, ``wc -l data.csv``, …)

The working directory is the caller's *branch's* workspace — materialized
fresh from the branch's current snapshot before the run and committed back
as a new snapshot after (see ``runtimes/staged.py``), not a bind-mount of a
shared filesystem tree. This is what makes the sandbox branch-aware: a run
on a forked branch never sees or mutates another branch's files, even
though both branches may share the same underlying blobs. Files written
there persist and are addressable from chat via the ``sandbox:`` scheme.
"""

from __future__ import annotations

import logging

import shlex
from typing import Any

from substrate.workspace import WorkspaceScope, workspace_scope
from substrate.workspace.layout import is_persistent_workspace
from substrate.types import scope_of
from substrate.tools import ToolExecutionResult
from substrate.tools import ToolRisk

from .code_risk import classify_and_summarize
from .runtimes.base import Mount, NetworkPolicy, SandboxRuntime, SandboxSpec, valid_mount_label
from .sandbox_response import (
    PRESENTATION_GUIDANCE,
    sandbox_error_result,
    sandbox_result_to_tool_result,
)

logger = logging.getLogger(__name__)

_DEFAULT_SESSION = "default"
_MAX_TIMEOUT = 300
# The most workspaces a run opens beside its own.
MAX_MOUNTS = 16


class CodeInterpreterTool:
    """Execute Python or shell in an isolated, session-scoped sandbox."""

    risk = ToolRisk.CRITICAL  # executes arbitrary code
    idempotent = False

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
        ctx: Any = None,
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
        run_scope = scope_of(ctx)
        thread_id = run_scope.workspace
        session_id = (
            thread_id
            if thread_id and thread_id != _DEFAULT_SESSION
            else self.session_id
        )
        scope = workspace_scope(run_scope, session_id)
        agent_id = run_scope.agent_id or "primary"
        parent_agent_id = run_scope.parent_agent_id
        if scope is None:
            return sandbox_error_result(
                "Sandbox execution requires a tenant-scoped, signed-in conversation."
            )
        if is_persistent_workspace(session_id):
            # An agent's home is one tree whichever conversation it is asked in: it has no branches.
            scope = scope.model_copy(update={"branch_id": "main"})
        mounts = _mounts(run_scope.mounts, scope, session_id)
        if isinstance(mounts, str):
            return sandbox_error_result(mounts)

        # Scratch-relative keys, not object-store keys: runtimes/staged.py
        # materializes the branch's workspace snapshot into local scratch
        # under session_key before the run and commits it back after.
        # private_key is deliberately NOT nested under session_key on the
        # host: materialize.py's commit() walks session_key's whole subtree
        # into the shared manifest, so a private dir nested inside it would
        # get committed and become visible to every other agent sharing
        # this branch — the opposite of what "private" means. nsjail still
        # bind-mounts it to the *virtual* path /workspace/private inside the
        # jail; only the real host paths need to stay siblings, not nested.
        session_key = f"{scope.conversation_id}/{scope.branch_id}"
        private_key = (
            f".private/{scope.conversation_id}/{scope.branch_id}/{parent_agent_id}/subagents/{agent_id}"
            if parent_agent_id
            else f".private/{scope.conversation_id}/{scope.branch_id}/{agent_id}"
        )

        spec = SandboxSpec(
            user_id=scope.user_id,
            thread_id=session_id,
            session_dir=session_key,
            tenant_id=scope.tenant_id,
            extra={"private_dir": private_key, "workspace_scope": scope},
            code=code or None,
            argv=shlex.split(command) if command else None,
            timeout_s=timeout_s,
            network=self._network,
            memory_bytes=self._memory_bytes,
            mounts=mounts,
        )

        logger.info(
            "code_interpreter[%s/%s]: %s via %s (timeout=%ds)",
            scope.user_id,
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

        if result.workspace_snapshot_id is not None and ctx is not None:
            record = getattr(ctx, "record_workspace_snapshot", None)
            if record is not None:
                record(result.workspace_snapshot_id)

        return sandbox_result_to_tool_result(result.to_sandbox_response())

    async def stop(self) -> None:
        """Release backend resources. Picked up automatically by the monolith's
        shutdown loop, which duck-types ``hasattr(tool, "stop")``."""
        await self._runtime.stop()


def _mounts(wanted: tuple[tuple[str, str], ...], scope: WorkspaceScope, own: str) -> tuple[Mount, ...] | str:
    """The workspaces to open beside the run's own, or a message saying why they cannot be. They come from the run's scope, set by whoever
    started it, and each must be an agent's home or a group's drive: nothing the model wrote reaches here, and nothing else can be mounted."""
    if len(wanted) > MAX_MOUNTS:
        return f"Cannot mount {len(wanted)} workspaces; at most {MAX_MOUNTS}."
    mounts: list[Mount] = []
    for label, workspace in wanted:
        if not valid_mount_label(label):
            return f"Invalid workspace folder name {label!r}."
        if not is_persistent_workspace(workspace):
            return f"Cannot mount {workspace!r}: only an agent's home or a group's drive can be."
        if workspace == own:
            continue
        mounts.append(
            Mount(
                label=label,
                session_dir=f"{workspace}/main",
                scope=WorkspaceScope(tenant_id=scope.tenant_id, user_id=scope.user_id, conversation_id=workspace, branch_id="main"),
            )
        )
    return tuple(mounts)


__all__ = ["CodeInterpreterTool", "MAX_MOUNTS"]
