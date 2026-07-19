"""CodeInterpreter — executes code in sandboxed container environments.

Two deployment targets:

* **K8sSandboxCodeInterpreterTool** — Kubernetes agent-sandbox (kubernetes-sigs).
  One pod-per-session via CRD; no privileged pods required.  Preferred for
  Kind / EKS / GKE cluster deployments.

* **LocalSandboxCodeInterpreterTool** — the same sandbox container
  (code_interpreter/agent-sandbox/Dockerfile) run directly via docker-compose
  for local dev, talked to over plain HTTP — no Kubernetes required.
"""

from __future__ import annotations

from .code_interpreter import (
    K8sSandboxCodeInterpreterTool,
    LocalSandboxCodeInterpreterTool,
)

__all__ = [
    "K8sSandboxCodeInterpreterTool",
    "LocalSandboxCodeInterpreterTool",
]
