"""RestateClient — HTTP wrapper around the Restate ingress and admin APIs.

Used by the monolith/gateway to:

- Start a durable agent workflow.
- Resolve HITL promises (tool approval, human input).
- Cancel a running workflow.
- Register a worker deployment with the Restate admin.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_SERVICE_NAME = "AgentWorkflow"


class RestateClient:
    """Thin HTTP client for the Restate ingress and admin APIs.

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

    async def start_workflow(
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
        """Start a durable agent workflow.

        Returns the Restate workflow ID (defaults to *thread_id*).
        """
        wf_id = workflow_id or thread_id
        safe_wf_id = quote(wf_id, safe="")

        payload = {
            "thread_id": thread_id,
            "user_content": user_content,
            "claims": claims,
            "model": model,
            "max_iterations": max_iterations,
            "system_instructions": system_instructions,
        }

        url = f"{self._ingress_url}/{_SERVICE_NAME}/{safe_wf_id}/run/send"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()

        logger.info("Started workflow %s for thread %s", wf_id, thread_id)
        return wf_id

    async def resolve_promise(
        self,
        workflow_id: str,
        handler_name: str,
        value: Dict[str, Any],
    ) -> None:
        """Call a workflow handler to resolve a durable promise.

        Args:
            workflow_id: The Restate workflow ID (typically thread_id).
            handler_name: Handler name (``"resolve_approval"`` or
                ``"resolve_human_input"``).
            value: Payload dict forwarded to the handler.
        """
        safe_wf_id = quote(workflow_id, safe="")
        safe_handler = quote(handler_name, safe="")
        url = f"{self._ingress_url}/{_SERVICE_NAME}/{safe_wf_id}/{safe_handler}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=json.dumps(value),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()

        logger.info("Resolved %s on workflow %s", handler_name, workflow_id)

    async def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel a running workflow via the Restate admin API."""
        safe_wf_id = quote(workflow_id, safe="")
        url = f"{self._admin_url}/invocations/{safe_wf_id}"

        async with httpx.AsyncClient() as client:
            resp = await client.delete(url, timeout=self._timeout)
            resp.raise_for_status()

        logger.info("Cancelled workflow %s", workflow_id)

    async def register_deployment(
        self,
        deployment_url: str,
        *,
        use_http11: bool = True,
    ) -> None:
        """Register a worker deployment with the Restate admin.

        Args:
            deployment_url: The URL where the worker serves Restate handlers.
            use_http11: Use HTTP/1.1 (default ``True``; required for uvicorn).
        """
        url = f"{self._admin_url}/deployments"
        payload = {"uri": deployment_url, "use_http_11": use_http11}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
            resp.raise_for_status()

        logger.info("Registered deployment %s with Restate", deployment_url)
