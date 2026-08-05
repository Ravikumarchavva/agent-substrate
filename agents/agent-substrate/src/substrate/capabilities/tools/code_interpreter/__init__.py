"""CodeInterpreter — executes agent-generated code in an isolated sandbox.

One LLM-facing tool (:class:`CodeInterpreterTool`) over a pluggable
:class:`SandboxRuntime`:

* ``BubblewrapRuntime`` — Linux namespaces on a single host. No daemon, no root,
  no nested virtualization. The default for single-node deployments.
* ``K8sRuntime`` — one agent-sandbox pod per session, per-user PVC ``subPath``,
  optional gVisor RuntimeClass. For cluster deployments.
* ``InProcessRuntime`` — no isolation; tests/CI only.
"""

from __future__ import annotations

from .code_interpreter import (
    BubblewrapRuntime,
    CodeInterpreterTool,
    InProcessRuntime,
    NetworkPolicy,
    SandboxRuntime,
    SandboxSpec,
    SandboxUnavailableError,
)

__all__ = [
    "BubblewrapRuntime",
    "CodeInterpreterTool",
    "InProcessRuntime",
    "NetworkPolicy",
    "SandboxRuntime",
    "SandboxSpec",
    "SandboxUnavailableError",
]
