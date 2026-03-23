"""Gateway BFF — HTTP clients for downstream service calls.

All downstream communication goes through typed clients with retries,
timeouts, and circuit breaking. Services never call each other directly
from route handlers.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class ServiceClient:
    """Base HTTP client for calling downstream services."""

    def __init__(
        self,
        base_url: str,
        service_name: str,
        timeout: float = 10.0,
        service_token: str = "",
    ):
        self._base_url = base_url.rstrip("/")
        self._service_name = service_name
        self._timeout = timeout
        self._service_token = service_token
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    def _headers(self, auth_token: str = "") -> dict:
        headers = {"Content-Type": "application/json"}
        token = auth_token or self._service_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def get(self, path: str, auth_token: str = "", **kwargs) -> httpx.Response:
        return await self._client.get(
            path,
            headers=self._headers(auth_token),
            **kwargs,
        )

    async def post(self, path: str, auth_token: str = "", **kwargs) -> httpx.Response:
        return await self._client.post(
            path,
            headers=self._headers(auth_token),
            **kwargs,
        )

    async def patch(self, path: str, auth_token: str = "", **kwargs) -> httpx.Response:
        return await self._client.patch(
            path,
            headers=self._headers(auth_token),
            **kwargs,
        )

    async def delete(self, path: str, auth_token: str = "", **kwargs) -> httpx.Response:
        return await self._client.delete(
            path,
            headers=self._headers(auth_token),
            **kwargs,
        )


class IdentityClient(ServiceClient):
    """Client for the Identity Auth service."""

    def __init__(self, base_url: str = "http://localhost:8010", **kw):
        super().__init__(base_url, "identity", **kw)

    async def exchange_token(self, frontend_token: str) -> dict:
        resp = await self.post("/auth/token", json={"frontend_token": frontend_token})
        resp.raise_for_status()
        return resp.json()

    async def refresh_token(self, refresh_token: str) -> dict:
        resp = await self.post("/auth/refresh", json={"refresh_token": refresh_token})
        resp.raise_for_status()
        return resp.json()

    async def get_me(self, auth_token: str) -> dict:
        resp = await self.get("/auth/me", auth_token=auth_token)
        resp.raise_for_status()
        return resp.json()


class PolicyClient(ServiceClient):
    """Client for the Policy Authorization service."""

    def __init__(self, base_url: str = "http://localhost:8011", **kw):
        super().__init__(base_url, "policy", **kw)

    async def check(
        self, auth_token: str, action: str, resource_type: str = "*"
    ) -> bool:
        resp = await self.post(
            "/policy/check",
            auth_token=auth_token,
            json={"action": action, "resource_type": resource_type},
        )
        resp.raise_for_status()
        return resp.json().get("allowed", False)


class ConversationClient(ServiceClient):
    """Client for the Conversation service."""

    def __init__(self, base_url: str = "http://localhost:8012", **kw):
        super().__init__(base_url, "conversation", **kw)

    async def create_thread(self, auth_token: str, name: str = "New Chat") -> dict:
        resp = await self.post("/threads", auth_token=auth_token, json={"name": name})
        resp.raise_for_status()
        return resp.json()

    async def list_threads(
        self, auth_token: str, limit: int = 50, offset: int = 0
    ) -> list:
        resp = await self.get(
            "/threads",
            auth_token=auth_token,
            params={"limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_thread(self, auth_token: str, thread_id: str) -> dict:
        resp = await self.get(f"/threads/{thread_id}", auth_token=auth_token)
        resp.raise_for_status()
        return resp.json()

    async def update_thread(self, auth_token: str, thread_id: str, payload: dict) -> dict:
        resp = await self.patch(f"/threads/{thread_id}", auth_token=auth_token, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def delete_thread(self, auth_token: str, thread_id: str) -> None:
        resp = await self.delete(f"/threads/{thread_id}", auth_token=auth_token)
        resp.raise_for_status()

    async def get_messages(self, auth_token: str, thread_id: str) -> list:
        resp = await self.get(f"/threads/{thread_id}/messages", auth_token=auth_token)
        resp.raise_for_status()
        return resp.json()


class WorkflowClient(ServiceClient):
    """Client for the Workflow Orchestrator service."""

    def __init__(self, base_url: str = "http://localhost:8013", **kw):
        super().__init__(base_url, "workflow", **kw)

    async def start_run(self, auth_token: str, payload: dict) -> dict:
        resp = await self.post("/jobs/runs", auth_token=auth_token, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def cancel_run(self, auth_token: str, thread_id: str) -> dict:
        # Uses /threads/{thread_id}/cancel to avoid path ambiguity with
        # /runs/{run_id}/cancel (same URL template, first route would win).
        resp = await self.post(
            f"/jobs/threads/{thread_id}/cancel",
            auth_token=auth_token,
        )
        resp.raise_for_status()
        return resp.json()


class HITLClient(ServiceClient):
    """Client for the HITL Approval service."""

    def __init__(self, base_url: str = "http://localhost:8016", **kw):
        super().__init__(base_url, "hitl", **kw)

    async def respond(self, auth_token: str, request_id: str, response: dict) -> dict:
        resp = await self.post(
            f"/hitl/respond/{request_id}",
            auth_token=auth_token,
            json=response,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_status(self, auth_token: str, thread_id: str) -> dict:
        resp = await self.get(f"/hitl/status/{thread_id}", auth_token=auth_token)
        resp.raise_for_status()
        return resp.json()


class StreamClient(ServiceClient):
    """Client for the Stream Projection service."""

    def __init__(self, base_url: str = "http://localhost:8017", **kw):
        super().__init__(base_url, "stream", **kw)


class ArtifactClient(ServiceClient):
    """Client for the Artifact service."""

    def __init__(self, base_url: str = "http://localhost:8018", **kw):
        super().__init__(base_url, "artifact", **kw)

    async def list_files(self, auth_token: str, thread_id: str) -> list:
        resp = await self.get(f"/artifacts/{thread_id}/files", auth_token=auth_token)
        resp.raise_for_status()
        return resp.json()


class CodeInterpreterServiceClient(ServiceClient):
    """Thin proxy client for the Code Interpreter service.

    The Gateway exposes /api/execute routes so that the frontend /
    notebooks can run code directly (outside of an agent loop) and
    receive multimodal outputs (text, images, files).

    For agent-driven execution the Tool Executor talks directly to the
    same CI service — this client is only used by the Gateway proxy.
    """

    def __init__(self, base_url: str = "http://localhost:8020", **kw):
        # code-interpreter service responds slowly (VM boot/execution)
        super().__init__(base_url, "code-interpreter", timeout=360.0, **kw)

    async def execute(self, session_id: str, payload: dict) -> dict:
        resp = await self.post(
            "/v1/execute",
            json={"session_id": session_id, **payload},
        )
        resp.raise_for_status()
        return resp.json()

    async def health(self) -> dict:
        resp = await self.get("/v1/health")
        resp.raise_for_status()
        return resp.json()

    async def list_sessions(self) -> dict:
        resp = await self.get("/v1/sessions")
        resp.raise_for_status()
        return resp.json()

    async def reset_session(self, session_id: str) -> dict:
        resp = await self.post(f"/v1/sessions/{session_id}/reset")
        resp.raise_for_status()
        return resp.json()

    async def destroy_session(self, session_id: str) -> dict:
        resp = await self._client.request(
            "DELETE",
            f"/v1/sessions/{session_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()
