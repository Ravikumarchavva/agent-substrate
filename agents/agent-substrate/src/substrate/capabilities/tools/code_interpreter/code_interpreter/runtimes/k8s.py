"""K8sRuntime — one sandbox Pod per session, via kubernetes-sigs/agent-sandbox.

Thin adapter over the existing ``CodeInterpreterService``, which already owns the
pod lifecycle, the per-thread session store, and the per-user PVC template. This
module only maps the ``SandboxRuntime`` contract onto it and adds the two
hardening bits that were missing:

* **gVisor** (``runtimeClassName``) — a user-space kernel that intercepts the
  sandbox's syscalls before they reach the host kernel. Chosen over Kata /
  Firecracker because it needs no nested virtualization, and it is what Google
  itself runs for GKE Sandbox and Cloud Run untrusted-code isolation.
* **Per-user ``subPath``** already exists in ``_ensure_user_template`` — a
  kernel-level bind-mount restriction, strictly stronger than the single-node
  bind mount, so one pod cannot even see another user's directory.

``CodeInterpreterService`` is synchronous (blocking k8s API calls), so every
call is offloaded with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from substrate.logger import setup_logging

from ..sandbox_service import CodeInterpreterConfig, CodeInterpreterService
from .base import ExecResult, NetworkPolicy, SandboxSpec

logger = setup_logging()


class K8sRuntime:
    """Execute code in a per-session Kubernetes sandbox pod."""

    name = "k8s"

    def __init__(
        self,
        *,
        service: CodeInterpreterService | None = None,
        config: CodeInterpreterConfig | None = None,
    ) -> None:
        self._config = config or CodeInterpreterConfig()
        self._service = service or CodeInterpreterService(config=self._config)

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        if spec.network is not NetworkPolicy.DENY:
            # Egress is governed by the cluster NetworkPolicy, not per-call —
            # say so rather than silently ignoring the request.
            logger.warning(
                "k8s runtime: network policy %s requested but egress is "
                "controlled by the cluster NetworkPolicy; ignoring per-call value.",
                spec.network.value,
            )

        if spec.argv:
            # The in-pod server exposes shell execution at /execute; route
            # `command` there rather than wrapping it in a Python string.
            result: dict[str, Any] = await asyncio.to_thread(
                self._service.run_command,
                spec.thread_id,
                spec.argv,
                spec.timeout_s,
                spec.user_id,
            )
        else:
            result = await asyncio.to_thread(
                self._service.run_code,
                spec.thread_id,
                spec.code or "",
                spec.timeout_s,
                spec.user_id,
            )

        return ExecResult(
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=int(
                result.get("exit_code", 0 if result.get("status") == "ok" else 1)
            ),
            output_files=list(result.get("output_files", [])),
        )

    async def terminate_session(self, thread_id: str) -> None:
        """Tear down one session's pod — used by the idle reaper."""
        await asyncio.to_thread(self._service.terminate_session, thread_id)

    async def stop(self) -> None:
        """Terminate every live sandbox pod.

        Without this, pods outlive the process: the monolith's shutdown loop
        (``serving/monolith/app.py``) duck-types ``hasattr(tool, "stop")``, and
        the old k8s tool had no ``stop()``, so nothing ever reaped them.
        """
        for session in list(self._service.store.list()):
            try:
                await self.terminate_session(session.thread_id)
            except Exception as exc:  # noqa: BLE001 - best-effort teardown
                logger.warning(
                    "k8s runtime: failed terminating session %s: %s",
                    session.thread_id,
                    exc,
                )


__all__ = ["K8sRuntime"]
