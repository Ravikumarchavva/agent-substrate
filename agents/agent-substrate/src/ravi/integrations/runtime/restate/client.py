"""RestateWorkflowClient — HTTP client for durable Restate workflows.

Unified replacement for the former ``TemporalClient`` (catalog/_temporal/)
and ``RestateClient`` (distributed/).  Provides the same method surface
the trigger system and workflow routes expect:

- ``start_pipeline_workflow()``
- ``start_chain_workflow()``
- ``start_agent_workflow()``
- ``query_workflow()``
- ``cancel_workflow()``
- ``get_result()``
- ``signal_workflow()``
- ``resolve_promise()``
- ``register_deployment()``
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Service names matching the Restate workflow definitions
_PIPELINE_SERVICE = "PipelineWorkflow"
_CHAIN_SERVICE = "ChainWorkflow"
_AGENT_SERVICE = "AgentWorkflow"


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class RestateWorkflowClient:
    """Thin HTTP client for Restate ingress + admin APIs.

    Drop-in replacement for the old ``TemporalClient``.  The trigger
    system calls ``set_temporal(client)`` — this class honours that
    duck-typed contract (``start_pipeline_workflow``,
    ``start_chain_workflow``, ``cancel_workflow``).

    Parameters
    ----------
    ingress_url:
        Restate ingress endpoint (default ``http://localhost:8080``).
    admin_url:
        Restate admin endpoint (default ``http://localhost:9070``).
    timeout:
        Default request timeout in seconds (default 30).
    """

    def __init__(
        self,
        ingress_url: str = "http://localhost:8080",
        admin_url: str = "http://localhost:9070",
        timeout: float = 30.0,
    ) -> None:
        self._ingress_url = ingress_url.rstrip("/")
        self._admin_url = admin_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Create the shared HTTP client (mirrors old TemporalClient.connect)."""
        self._http = httpx.AsyncClient(timeout=self._timeout)
        logger.info(
            "RestateWorkflowClient ready (ingress=%s, admin=%s)",
            self._ingress_url,
            self._admin_url,
        )

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "RestateWorkflowClient not connected — call connect() first"
            )
        return self._http

    # -- Workflow dispatch --------------------------------------------------

    async def start_pipeline_workflow(
        self,
        pipeline_name: str,
        definition: dict[str, Any],
        *,
        workflow_id: str | None = None,
    ) -> str:
        """Start a durable pipeline workflow.

        Compatible with the old ``TemporalClient.start_pipeline_workflow``
        signature used by the trigger system.
        """
        wf_id = workflow_id or f"pipeline-{pipeline_name}-{_short_id()}"
        await self._invoke(
            _PIPELINE_SERVICE,
            wf_id,
            "run",
            payload=definition,
            send=True,
        )
        logger.info("Started PipelineWorkflow %s", wf_id)
        return wf_id

    async def start_chain_workflow(
        self,
        code: str,
        description: str,
        *,
        timeout: int = 120,
        workflow_id: str | None = None,
    ) -> str:
        """Start a durable code-chain workflow.

        Compatible with the old ``TemporalClient.start_chain_workflow``
        signature used by the trigger system.
        """
        wf_id = workflow_id or f"chain-{_short_id()}"
        await self._invoke(
            _CHAIN_SERVICE,
            wf_id,
            "run",
            payload={
                "code": code,
                "description": description,
                "timeout": timeout,
            },
            send=True,
        )
        logger.info("Started ChainWorkflow %s", wf_id)
        return wf_id

    async def start_agent_workflow(
        self,
        thread_id: str,
        user_content: str,
        claims: Dict[str, Any],
        *,
        model: str = "gpt-4o-mini",
        max_iterations: int = 30,
        system_instructions: str = "You are a helpful agent.",
        workflow_id: Optional[str] = None,
    ) -> str:
        """Start a durable ReAct agent workflow."""
        wf_id = workflow_id or thread_id
        await self._invoke(
            _AGENT_SERVICE,
            wf_id,
            "run",
            payload={
                "thread_id": thread_id,
                "user_content": user_content,
                "claims": claims,
                "model": model,
                "max_iterations": max_iterations,
                "system_instructions": system_instructions,
            },
            send=True,
        )
        logger.info("Started AgentWorkflow %s for thread %s", wf_id, thread_id)
        return wf_id

    # -- Query / control ----------------------------------------------------

    async def query_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Query a running workflow for its current status.

        Uses Restate admin API to fetch invocation status.
        """
        safe_wf = quote(workflow_id, safe="")
        resp = await self._client.get(
            f"{self._admin_url}/invocations/{safe_wf}",
        )
        if resp.status_code == 404:
            return {"workflow_id": workflow_id, "status": "NOT_FOUND"}
        resp.raise_for_status()
        data = resp.json()
        return {
            "workflow_id": workflow_id,
            "status": data.get("status", "UNKNOWN"),
            "start_time": data.get("created_at"),
            "close_time": data.get("completed_at"),
        }

    async def get_result(self, workflow_id: str) -> Any:
        """Wait for and return the workflow result.

        Uses a blocking Restate ingress call (no ``/send`` suffix).
        """
        # Try pipeline first, then chain, then agent
        for service in (_PIPELINE_SERVICE, _CHAIN_SERVICE, _AGENT_SERVICE):
            try:
                return await self._invoke(service, workflow_id, "run", send=False)
            except httpx.HTTPStatusError:
                continue
        raise RuntimeError(f"Could not retrieve result for workflow {workflow_id}")

    async def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel a running workflow via the Restate admin API."""
        safe_wf = quote(workflow_id, safe="")
        resp = await self._client.delete(
            f"{self._admin_url}/invocations/{safe_wf}",
        )
        if resp.status_code != 404:
            resp.raise_for_status()
        logger.info("Cancelled workflow %s", workflow_id)

    async def signal_workflow(
        self,
        workflow_id: str,
        signal_name: str,
        payload: Any = None,
    ) -> None:
        """Send a signal (shared handler call) to a running workflow.

        Maps to a Restate shared handler invocation.
        """
        await self._invoke(
            _AGENT_SERVICE,
            workflow_id,
            signal_name,
            payload=payload,
        )
        logger.info("Sent signal '%s' to workflow %s", signal_name, workflow_id)

    # -- HITL promise resolution -------------------------------------------

    async def resolve_promise(
        self,
        workflow_id: str,
        handler_name: str,
        value: Dict[str, Any],
    ) -> None:
        """Resolve an HITL promise via a shared handler call.

        Args:
            workflow_id: Restate workflow ID.
            handler_name: ``"resolve_approval"`` or ``"resolve_human_input"``.
            value: Payload forwarded to the handler.
        """
        await self._invoke(
            _AGENT_SERVICE,
            workflow_id,
            handler_name,
            payload=value,
        )
        logger.info("Resolved %s on workflow %s", handler_name, workflow_id)

    # -- Deployment registration -------------------------------------------

    async def register_deployment(
        self,
        deployment_url: str,
        *,
        use_http11: bool = True,
    ) -> None:
        """Register a worker deployment with the Restate admin."""
        resp = await self._client.post(
            f"{self._admin_url}/deployments",
            content=json.dumps({"uri": deployment_url, "use_http_11": use_http11}),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        logger.info("Registered deployment %s with Restate", deployment_url)

    # -- Internal ----------------------------------------------------------

    async def _invoke(
        self,
        service: str,
        key: str,
        handler: str,
        *,
        payload: Any = None,
        send: bool = False,
    ) -> Any:
        """Call a Restate virtual-object handler.

        Args:
            service: Service name (e.g. ``"PipelineWorkflow"``).
            key: Workflow / object key.
            handler: Handler name (e.g. ``"run"``).
            payload: JSON-serializable payload (optional).
            send: If ``True``, use ``/send`` suffix (fire-and-forget).
        """
        safe_service = quote(service, safe="")
        safe_key = quote(key, safe="")
        safe_handler = quote(handler, safe="")

        path = f"/{safe_service}/{safe_key}/{safe_handler}"
        if send:
            path += "/send"

        body = json.dumps(payload) if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}

        resp = await self._client.post(
            f"{self._ingress_url}{path}",
            content=body,
            headers=headers,
        )
        resp.raise_for_status()

        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text or None
