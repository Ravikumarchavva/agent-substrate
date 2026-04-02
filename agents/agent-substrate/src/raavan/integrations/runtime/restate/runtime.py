"""RestateRuntime — durable ``AgentRuntime`` backed by Restate.

Registers agent handlers as Restate virtual-object methods so that
every ``send_message`` becomes a durable, journalled invocation.
Tool calls wrapped in ``ctx.run()`` gain exactly-once semantics.
HITL flows use ``ctx.promise()`` to suspend with zero resources.

Key patterns ported from the former ``distributed/workflow.py``:
- ``ctx.run("name", fn)`` — checkpoint each tool execution
- ``ctx.promise("approval-{id}")`` — suspend for HITL approval
- ``ctx.rand.uuid4()`` — deterministic idempotency keys

Usage::

    runtime = RestateRuntime(
        ingress_url="http://localhost:8080",
        admin_url="http://localhost:9070",
    )
    await runtime.register("chat_agent", my_handler)
    await runtime.start()

    result = await runtime.send_message(
        payload,
        sender=AgentId("caller", "1"),
        recipient=AgentId("chat_agent", "abc"),
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict
from urllib.parse import quote

from raavan.core.runtime._protocol import AgentId
from raavan.integrations.runtime._base import BaseRemoteRuntime

logger = logging.getLogger(__name__)

try:
    import restate  # noqa: F401
    import httpx

    _HAS_RESTATE = True
except ImportError:
    _HAS_RESTATE = False

# Workflow service name prefix for Restate virtual objects
_SERVICE_PREFIX = "AgentHandler"


class RestateRuntime(BaseRemoteRuntime):
    """Restate-backed ``AgentRuntime`` with durable execution.

    Inherits all local handler management from :class:`BaseRemoteRuntime`
    and adds:
    - Restate deployment registration on startup
    - Remote dispatch via Restate ingress HTTP calls
    - Durable promise helpers for HITL flows

    Parameters
    ----------
    ingress_url:
        Restate ingress endpoint (default ``http://localhost:8080``).
    admin_url:
        Restate admin endpoint (default ``http://localhost:9070``).
    worker_url:
        URL where this worker's Restate ASGI app is served.
        Used for deployment registration.
    admin_timeout:
        Timeout in seconds for admin API calls (default 15).
    ingress_timeout:
        Timeout in seconds for ingress API calls (default 30).
    promise_timeout:
        Timeout in seconds for promise resolution calls (default 10).
    """

    def __init__(
        self,
        ingress_url: str = "http://localhost:8080",
        admin_url: str = "http://localhost:9070",
        worker_url: str = "http://localhost:9080",
        admin_timeout: float = 15.0,
        ingress_timeout: float = 30.0,
        promise_timeout: float = 10.0,
    ) -> None:
        if not _HAS_RESTATE:
            raise ImportError(
                "restate-sdk and httpx are required for RestateRuntime. "
                "Install with: uv add restate-sdk httpx"
            )
        super().__init__()
        self._ingress_url = ingress_url.rstrip("/")
        self._admin_url = admin_url.rstrip("/")
        self._worker_url = worker_url.rstrip("/")
        self._admin_timeout = admin_timeout
        self._ingress_timeout = ingress_timeout
        self._promise_timeout = promise_timeout
        self._restate_app: Any = None

    # -- Transport lifecycle ------------------------------------------------

    async def start(self) -> None:
        """Register this deployment with the Restate admin API.

        H9 fix: ``_started`` is only set to ``True`` after successful
        registration.
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._admin_url}/deployments",
                    content=json.dumps({"uri": self._worker_url, "use_http_11": True}),
                    headers={"Content-Type": "application/json"},
                    timeout=self._admin_timeout,
                )
                resp.raise_for_status()
                logger.info(
                    "Registered deployment %s with Restate admin",
                    self._worker_url,
                )
        except Exception as exc:
            logger.warning("Failed to register with Restate admin: %s", exc)
            # H9 fix: do NOT set _started if admin registration failed
            raise RuntimeError(
                f"RestateRuntime failed to register with admin: {exc}"
            ) from exc

        self._started = True
        logger.info("RestateRuntime started (ingress=%s)", self._ingress_url)

    async def stop(self) -> None:
        """Shut down the Restate runtime."""
        try:
            self._started = False
        finally:
            # H12 fix: ensure cleanup runs in finally
            self._restate_app = None
            logger.info("RestateRuntime stopped")

    # -- Remote transport ---------------------------------------------------

    async def _remote_send(
        self,
        message: Any,
        *,
        sender: AgentId | None,
        recipient: AgentId,
    ) -> Any:
        """Invoke a remote agent as a Restate virtual-object handler.

        H10 fix: URL-encodes service_name and recipient.key.
        """
        service_name = f"{_SERVICE_PREFIX}_{recipient.type}"
        # H10 fix: prevent URL path injection
        safe_service = quote(service_name, safe="")
        safe_key = quote(recipient.key, safe="")
        url = f"{self._ingress_url}/{safe_service}/{safe_key}/handle"

        payload = json.dumps(
            {
                "sender": {"type": sender.type, "key": sender.key} if sender else None,
                "payload": message,
            }
        )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    content=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self._ingress_timeout,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"RestateRuntime: remote send to {recipient} failed: {exc}"
            ) from exc

    # -- Durable helpers (for use within Restate handler context) -----------

    async def resolve_promise(
        self,
        workflow_id: str,
        promise_name: str,
        value: Dict[str, Any],
    ) -> None:
        """Resolve a durable promise in a running Restate workflow.

        Used by HITL endpoints when a user approves/rejects a tool call
        or provides human input.
        """
        # H10 fix: URL-encode path segments
        safe_wf = quote(workflow_id, safe="")
        safe_promise = quote(promise_name, safe="")
        url = f"{self._ingress_url}/AgentWorkflow/{safe_wf}/{safe_promise}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    content=json.dumps(value),
                    headers={"Content-Type": "application/json"},
                    timeout=self._promise_timeout,
                )
                resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"RestateRuntime: resolve_promise failed: {exc}"
            ) from exc

        logger.info("Resolved promise %s on workflow %s", promise_name, workflow_id)

    async def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel a running Restate workflow."""
        safe_wf = quote(workflow_id, safe="")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{self._admin_url}/invocations?workflow_id={safe_wf}",
                    timeout=self._admin_timeout,
                )
                if resp.status_code != 404:
                    resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"RestateRuntime: cancel_workflow failed: {exc}"
            ) from exc
        logger.info("Cancelled workflow %s", workflow_id)
