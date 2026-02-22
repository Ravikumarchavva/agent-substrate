"""HTTP client for the Code Interpreter service.

Used by the main backend to communicate with code-interpreter pod(s).

Supports two routing modes:

1. **Single URL** (local dev / single replica)::

       client = CodeInterpreterClient(base_url="http://localhost:8080")

2. **StatefulSet discovery** (multi-replica, consistent-hash routing)::

       client = CodeInterpreterClient(
           headless_service="code-interpreter-headless",
           namespace="agent-framework",
           replicas=2,
       )
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from agent_framework.code_interpreter_service.schemas import (
    ExecuteResponse,
    FileReadResponse,
    HealthResponse,
    SessionListResponse,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)


class CodeInterpreterClient:
    """Async HTTP client for the code-interpreter service."""

    def __init__(
        self,
        base_url: str = "http://code-interpreter:8080",
        replicas: int = 1,
        headless_service: str = "",
        namespace: str = "agent-framework",
        port: int = 8080,
        auth_token: str = "",
    ):
        self._auth_token = auth_token
        self._headers: dict[str, str] = {}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"

        if headless_service and replicas > 1:
            self._pod_urls = [
                (
                    f"http://code-interpreter-{i}.{headless_service}"
                    f".{namespace}.svc.cluster.local:{port}"
                )
                for i in range(replicas)
            ]
            logger.info("CI client: StatefulSet mode  replicas=%d", replicas)
        else:
            self._pod_urls = [base_url.rstrip("/")]
            logger.info("CI client: single-URL mode -> %s", base_url)

        self._clients: dict[str, httpx.AsyncClient] = {}

    def _get_client(self, url: str) -> httpx.AsyncClient:
        if url not in self._clients:
            self._clients[url] = httpx.AsyncClient(
                base_url=url,
                timeout=_DEFAULT_TIMEOUT,
                headers=self._headers,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._clients[url]

    def _route(self, session_id: str) -> str:
        """Consistent-hash a session_id to a pod URL."""
        digest = int(hashlib.md5(session_id.encode()).hexdigest(), 16)
        return self._pod_urls[digest % len(self._pod_urls)]

    async def _request(self, method: str, url: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._get_client(url)
        resp = await client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp

    # ── Execute ──────────────────────────────────────────────────────────

    async def execute(
        self, session_id: str, code: str,
        exec_type: str = "python", timeout: int = 30,
    ) -> ExecuteResponse:
        url = self._route(session_id)
        try:
            resp = await self._request("POST", url, "/v1/execute", json={
                "session_id": session_id, "code": code,
                "exec_type": exec_type, "timeout": timeout,
            })
            return ExecuteResponse(**resp.json())
        except httpx.HTTPStatusError as exc:
            logger.error("CI execute HTTP %d: %s", exc.response.status_code, exc.response.text[:500])
            return ExecuteResponse(
                success=False, session_id=session_id,
                error=f"Service error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.RequestError as exc:
            logger.error("CI execute connection error: %s", exc)
            return ExecuteResponse(
                success=False, session_id=session_id, error=f"Connection error: {exc}",
            )

    # ── Session management ───────────────────────────────────────────────

    async def list_sessions(self, pod_url: str | None = None) -> SessionListResponse:
        url = pod_url or self._pod_urls[0]
        resp = await self._request("GET", url, "/v1/sessions")
        return SessionListResponse(**resp.json())

    async def list_sessions_all_pods(self) -> list[SessionListResponse]:
        results = []
        for url in self._pod_urls:
            try:
                resp = await self._request("GET", url, "/v1/sessions")
                results.append(SessionListResponse(**resp.json()))
            except Exception as exc:
                logger.warning("Failed to list sessions on %s: %s", url, exc)
        return results

    async def destroy_session(self, session_id: str) -> dict:
        url = self._route(session_id)
        resp = await self._request("DELETE", url, f"/v1/sessions/{session_id}")
        return resp.json()

    async def reset_session(self, session_id: str) -> dict:
        url = self._route(session_id)
        resp = await self._request("POST", url, f"/v1/sessions/{session_id}/reset")
        return resp.json()

    async def get_state(self, session_id: str) -> dict:
        url = self._route(session_id)
        resp = await self._request("GET", url, f"/v1/sessions/{session_id}/state")
        return resp.json()

    # ── File operations ──────────────────────────────────────────────────

    async def write_file(self, session_id: str, path: str, content: str, encoding: str = "utf-8") -> dict:
        url = self._route(session_id)
        resp = await self._request(
            "POST", url, f"/v1/sessions/{session_id}/files/write",
            json={"path": path, "content": content, "encoding": encoding},
        )
        return resp.json()

    async def read_file(self, session_id: str, path: str) -> FileReadResponse:
        url = self._route(session_id)
        resp = await self._request("GET", url, f"/v1/sessions/{session_id}/files/read", params={"path": path})
        return FileReadResponse(**resp.json())

    async def read_file_binary(self, session_id: str, path: str) -> dict:
        url = self._route(session_id)
        resp = await self._request("GET", url, f"/v1/sessions/{session_id}/files/read_binary", params={"path": path})
        return resp.json()

    async def install_packages(self, session_id: str, packages: list[str]) -> dict:
        url = self._route(session_id)
        resp = await self._request("POST", url, f"/v1/sessions/{session_id}/install", json={"packages": packages})
        return resp.json()

    # ── Health ───────────────────────────────────────────────────────────

    async def health(self, pod_url: str | None = None) -> HealthResponse:
        url = pod_url or self._pod_urls[0]
        resp = await self._request("GET", url, "/v1/health")
        return HealthResponse(**resp.json())

    async def health_all_pods(self) -> list[HealthResponse]:
        results = []
        for url in self._pod_urls:
            try:
                resp = await self._request("GET", url, "/v1/health")
                results.append(HealthResponse(**resp.json()))
            except Exception as exc:
                logger.warning("Health check failed for %s: %s", url, exc)
        return results

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
