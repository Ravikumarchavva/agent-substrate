"""Chain bridge — engine-side handler for tool calls from the sandbox.

The bridge sits between the CodeInterpreter sandbox and the ``ToolInvoker``.
It provides a simple token-authenticated endpoint that the sandbox prelude
calls via HTTP (when the HTTP transport is enabled) or waits on a
control-channel marker (default, zero-egress transport).

Architecture
------------
Default transport: control-channel (zero sandbox egress)
    The sandbox writes a JSON marker to stdout and waits for the next cell
    result.  The ``ToolChainTool.execute()`` intercepts the marker, invokes
    the tool via ``ToolInvoker``, and injects the result as the next cell.
    No inbound network connection is needed from the sandbox.

Optional: HTTP callback (behind NetworkPolicy + one-time token)
    ``POST /internal/chain/{chain_id}/invoke`` with a per-chain bearer token
    bound to the ``ToolInvoker`` session.  Token is single-use per chain and
    invalidated when the chain ends.  Deploy only with a K8s NetworkPolicy
    that allows the sandbox to reach exactly this endpoint.

This module contains:
- ``ChainBridgeRegistry`` — manages active chain sessions for HTTP transport
- ``BridgeSession`` — per-chain state (token, invoker session)
- ``build_bridge_router`` — FastAPI router factory for the HTTP endpoint
"""

from __future__ import annotations

import secrets
from typing import Any

from substrate.agents.tools.invoker import InvokerSession, ToolInvoker
from substrate.kernel.tools.chain import InvocationResult
from substrate.kernel.tools import ToolCallRequest
from substrate.logger import setup_logging

logger = setup_logging("substrate.capabilities.tools.chain.bridge")


class BridgeSession:
    """Per-chain bridge state.  Created by ``ToolChainTool`` at chain start."""

    def __init__(
        self,
        chain_id: str,
        invoker: ToolInvoker,
        invoker_session: InvokerSession,
        ctx: Any | None = None,
        progress_sink: Any | None = None,
    ) -> None:
        self.chain_id = chain_id
        self.token = secrets.token_urlsafe(32)
        self._invoker = invoker
        self._invoker_session = invoker_session
        self._ctx = ctx
        self._progress_sink = progress_sink
        self._active = True

    def invalidate(self) -> None:
        self._active = False

    async def dispatch(self, call: ToolCallRequest) -> InvocationResult:
        if not self._active:
            return InvocationResult(
                status="error",
                text="Bridge session has been closed.",
            )
        return await self._invoker.invoke(
            call,
            session=self._invoker_session,
            ctx=self._ctx,
            progress_sink=self._progress_sink,
        )


class ChainBridgeRegistry:
    """Thread-safe registry of active bridge sessions (HTTP transport).

    ``ToolChainTool.execute()`` registers a ``BridgeSession`` before running
    the sandbox code and deregisters it in ``finally`` — token invalidation
    is guaranteed even on crash.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, BridgeSession] = {}

    def register(self, session: BridgeSession) -> None:
        self._sessions[session.chain_id] = session
        logger.debug("Registered chain bridge session %s", session.chain_id)

    def deregister(self, chain_id: str) -> None:
        session = self._sessions.pop(chain_id, None)
        if session is not None:
            session.invalidate()
        logger.debug("Deregistered chain bridge session %s", chain_id)

    def get(self, chain_id: str) -> BridgeSession | None:
        return self._sessions.get(chain_id)


def build_bridge_router(registry: ChainBridgeRegistry) -> Any:
    """Build the FastAPI router for the HTTP bridge endpoint.

    Mount via ``app.include_router(build_bridge_router(registry), prefix="/internal")``.
    Protected by bearer token; only valid for the lifetime of the chain.
    """
    try:
        from fastapi import APIRouter, HTTPException, Request
    except ImportError:
        return None

    router = APIRouter()

    @router.post("/chain/{chain_id}/invoke")
    async def invoke_tool(chain_id: str, body: dict, request: Request) -> dict:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = auth[7:]

        session = registry.get(chain_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Chain session not found")
        if not secrets.compare_digest(token, session.token):
            raise HTTPException(status_code=401, detail="Invalid chain token")

        try:
            call = ToolCallRequest(**body)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        result = await session.dispatch(call)
        return result.model_dump()

    return router


__all__ = ["BridgeSession", "ChainBridgeRegistry", "build_bridge_router"]
