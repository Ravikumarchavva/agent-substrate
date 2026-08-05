"""Sandbox execution backends behind one ``SandboxRuntime`` contract.

| Runtime              | Isolation                                  | Needs |
|----------------------|--------------------------------------------|-------|
| ``BubblewrapRuntime``| Linux namespaces (mount/pid/net/user)      | `bwrap`, unprivileged userns |
| ``K8sRuntime``       | Pod per session + per-user PVC subPath, optional gVisor | a cluster |
| ``InProcessRuntime`` | **none** — tests/CI only                   | nothing |

``K8sRuntime`` is imported lazily: it pulls in the Kubernetes client, which is
not installed in single-node deployments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import (
    ExecResult,
    NetworkPolicy,
    SandboxRuntime,
    SandboxSpec,
    SandboxUnavailableError,
)
from .bubblewrap import BubblewrapRuntime
from .inprocess import InProcessRuntime

if TYPE_CHECKING:
    from .k8s import K8sRuntime


def __getattr__(name: str) -> Any:
    if name == "K8sRuntime":
        from .k8s import K8sRuntime

        return K8sRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BubblewrapRuntime",
    "ExecResult",
    "InProcessRuntime",
    "K8sRuntime",
    "NetworkPolicy",
    "SandboxRuntime",
    "SandboxSpec",
    "SandboxUnavailableError",
]
