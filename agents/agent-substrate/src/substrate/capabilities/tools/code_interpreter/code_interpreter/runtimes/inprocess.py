"""InProcessRuntime — NO isolation. Tests and CI only.

Exists so the tool can be exercised without bubblewrap or a cluster (e.g. on
macOS dev machines, or in unit tests that only care about tool plumbing).

**Never select this in a deployment that serves more than one user.** It runs
LLM-generated code in this very process, so that code can read every user's
files, the host environment (API keys, JWT secret, DB URL), and mutate
interpreter state. ``serving_factory`` logs a loud warning when it is chosen.

Unlike the previous in-process runner it does *not* share one module-global
namespace across executions — each run gets a fresh ``globals()`` dict, so
variables never bleed between users even in this unsafe mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import traceback
from pathlib import Path
from typing import Any

from ._files import collect_changed, snapshot
from .base import ExecResult, SandboxSpec


class InProcessRuntime:
    """Run code directly in this process. No security boundary whatsoever."""

    name = "inprocess"

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root).resolve()

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        session_path = (self._root / spec.session_dir.strip("/")).resolve()
        session_path.mkdir(parents=True, exist_ok=True)
        before = snapshot(session_path)

        if spec.argv:
            return await self._run_argv(spec, session_path, before)
        if not spec.code:
            # Same contract as BubblewrapRuntime: runtimes never silently
            # succeed on an empty request.
            return ExecResult(stderr="No code or command supplied.", exit_code=2)

        stdout, stderr = io.StringIO(), io.StringIO()
        exit_code = 0
        cwd = os.getcwd()
        # A FRESH namespace per execution — no cross-execution/user bleed.
        namespace: dict[str, Any] = {"__name__": "__code_interpreter__"}
        try:
            os.chdir(session_path)
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exec(compile(spec.code or "", "<agent-code>", "exec"), namespace)  # noqa: S102
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except BaseException:  # noqa: BLE001 - report any failure to the agent
            stderr.write(traceback.format_exc())
            exit_code = 1
        finally:
            os.chdir(cwd)

        return ExecResult(
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            exit_code=exit_code,
            output_files=collect_changed(session_path, before),
        )

    async def _run_argv(
        self, spec: SandboxSpec, session_path: Path, before: dict[str, tuple[int, int]]
    ) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *(spec.argv or []),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(session_path),
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(
                stderr=f"Execution timed out after {spec.timeout_s}s.", exit_code=124
            )
        return ExecResult(
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
            exit_code=proc.returncode or 0,
            output_files=collect_changed(session_path, before),
        )

    async def stop(self) -> None:
        """Nothing to release."""


__all__ = ["InProcessRuntime"]
