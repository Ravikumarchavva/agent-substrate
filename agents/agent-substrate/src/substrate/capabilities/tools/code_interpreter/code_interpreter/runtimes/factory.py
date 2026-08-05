"""build_runtime — turn config into a concrete :class:`SandboxRuntime`.

One explicit switch, shared by the monolith and the ``tool_executor`` service so
both deployments select isolation the same way. Replaces the previous
try-K8s-except-fall-back-to-local guessing, which made the effective security
boundary depend on whether an import happened to throw.

Selection is **fail-closed**: if the configured runtime cannot isolate on this
host, this raises rather than silently downgrading to an unisolated backend.
"""

from __future__ import annotations

import os

from substrate.logger import setup_logging

from ..sandbox_service import CodeInterpreterConfig
from .base import NetworkPolicy, SandboxRuntime, SandboxUnavailableError
from .bubblewrap import BubblewrapRuntime
from .inprocess import InProcessRuntime

logger = setup_logging()


def network_policy(raw: str) -> NetworkPolicy:
    try:
        return NetworkPolicy(raw.strip().lower())
    except ValueError:
        logger.warning(
            "Unknown SANDBOX_NETWORK_POLICY %r; falling back to 'deny'.", raw
        )
        return NetworkPolicy.DENY


def build_runtime(
    kind: str,
    *,
    workspace_root: str,
    runtime_class_name: str = "",
    workspace_pvc_claim: str | None = None,
    python_bin: str = "",
) -> SandboxRuntime:
    """Construct the runtime named by *kind*.

    Raises ``SandboxUnavailableError`` for an unknown name or a runtime whose
    host prerequisites are missing — a hard failure at startup is much safer
    than discovering at request time that nothing is isolating the code.
    """
    name = kind.strip().lower()

    if name == "bubblewrap":
        runtime = BubblewrapRuntime(workspace_root, python_bin=python_bin)
        runtime.preflight()  # raises with actionable remediation if unusable
        logger.info(
            "Sandbox runtime: bubblewrap (namespace isolation, %s)", workspace_root
        )
        return runtime

    if name == "k8s":
        from .k8s import K8sRuntime

        config = CodeInterpreterConfig(
            template=os.environ.get("CI_SANDBOX_TEMPLATE", "python-sandbox-template"),
            namespace=os.environ.get("CI_SANDBOX_NAMESPACE", "default"),
            workspace_pvc_claim=workspace_pvc_claim,
            runtime_class_name=runtime_class_name,
        )
        logger.info(
            "Sandbox runtime: k8s (pod per session, runtimeClass=%s, pvc=%s)",
            runtime_class_name or "<cluster default>",
            workspace_pvc_claim or "<none>",
        )
        return K8sRuntime(config=config)

    if name == "inprocess":
        logger.warning(
            "Sandbox runtime: inprocess — NO ISOLATION. Agent-generated code runs "
            "in this process with full access to every user's files and the host "
            "environment. Acceptable for tests only; never serve users with this."
        )
        return InProcessRuntime(workspace_root)

    raise SandboxUnavailableError(
        f"Unknown SANDBOX_RUNTIME {kind!r}. Valid: bubblewrap, k8s, inprocess."
    )


__all__ = ["build_runtime", "network_policy"]
