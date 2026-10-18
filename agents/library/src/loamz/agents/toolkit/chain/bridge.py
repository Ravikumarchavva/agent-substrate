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

HTTP callback (not mounted by the server today)
    A per-chain bearer token (``BridgeSession.token``) bound to the ``ToolInvoker``
    session, invalidated when the chain ends: what a callback endpoint, deployed
    behind a NetworkPolicy, would check.

This module contains:
- ``ChainBridgeRegistry`` — manages active chain sessions for HTTP transport
- ``BridgeSession`` — per-chain state (token, invoker session)
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from loamz.agents.kernel.runtime.tool_invoker import InvokerSession, ToolInvoker
from loamz.agents.kernel.tools import InvocationResult, ToolCallRequest

logger = logging.getLogger(__name__)


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


__all__ = ["BridgeSession", "ChainBridgeRegistry"]
