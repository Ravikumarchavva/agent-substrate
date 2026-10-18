"""CodeInterpreter — executes agent-generated code in an isolated sandbox.

One LLM-facing tool (:class:`CodeInterpreterTool`) over a pluggable
:class:`SandboxRuntime`:

* ``NsjailRuntime`` — Linux namespaces + cgroups on a single host. No daemon,
  no root, no nested virtualization. The default for single-node deployments.
* ``K8sRuntime`` — one agent-sandbox pod per session, per-user PVC ``subPath``,
  optional gVisor RuntimeClass. For cluster deployments.
* ``InProcessRuntime`` — no isolation; tests/CI only.
"""

from __future__ import annotations

from .code_interpreter import (
    CodeInterpreterTool,
    InProcessRuntime,
    NetworkPolicy,
    NsjailRuntime,
    SandboxRuntime,
    SandboxSpec,
    SandboxUnavailableError,
)

__all__ = [
    "CodeInterpreterTool",
    "InProcessRuntime",
    "NetworkPolicy",
    "NsjailRuntime",
    "SandboxRuntime",
    "SandboxSpec",
    "SandboxUnavailableError",
]
