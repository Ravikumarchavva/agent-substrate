"""GrpcRuntime — gRPC-backed ``AgentRuntime`` implementation.

Routes ``send_message`` as unary gRPC calls and ``publish_message`` as
server-streaming pushes.  Local handlers are served via a gRPC servicer;
remote agents are dispatched via stubs.

This enables:
  - Running agents on separate machines/pods
  - Language-interop (any gRPC client can host an agent)
  - Load-balanced tool execution across replicas

Usage::

    runtime = GrpcRuntime(listen_address="0.0.0.0:50051")
    await runtime.register("chat_agent", my_handler)
    await runtime.start()

    # Remote dispatch
    response = await runtime.send_message(
        payload,
        sender=AgentId("caller", "1"),
        recipient=AgentId("chat_agent", "abc"),
    )

    await runtime.stop()

The server uses gRPC generic handlers — no ``.proto`` compilation required.
The wire format is JSON-encoded envelopes over unary RPCs at
``/raavan.runtime.AgentService/SendMessage``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from raavan.core.runtime._protocol import AgentId
from raavan.integrations.runtime._base import BaseRemoteRuntime

logger = logging.getLogger(__name__)

try:
    import grpc
    from grpc import aio as grpc_aio

    _HAS_GRPC = True
except ImportError:
    grpc = None  # type: ignore[assignment]
    grpc_aio = None  # type: ignore[assignment]
    _HAS_GRPC = False

_SERVICE_METHOD = "/raavan.runtime.AgentService/SendMessage"


if _HAS_GRPC:

    class _AgentServiceHandler(grpc.GenericRpcHandler):  # type: ignore[misc]
        """Generic gRPC handler that dispatches incoming calls to local agents.

        Routes the ``SendMessage`` unary RPC to the owning
        :class:`GrpcRuntime`'s local handler registry.
        """

        def __init__(self, runtime: GrpcRuntime) -> None:
            self._runtime = runtime

        def service(
            self, handler_call_details: grpc.HandlerCallDetails
        ) -> grpc.RpcMethodHandler | None:
            if handler_call_details.method == _SERVICE_METHOD:
                return grpc.unary_unary_rpc_method_handler(
                    self._handle_send_message,
                    request_deserializer=lambda b: json.loads(b.decode("utf-8")),
                    response_serializer=lambda obj: json.dumps(obj).encode("utf-8"),
                )
            return None

        async def _handle_send_message(
            self,
            request: dict[str, Any],
            context: grpc.aio.ServicerContext,
        ) -> Any:
            """Deserialise the JSON envelope and dispatch locally."""
            recipient = AgentId(
                type=request["agent_type"],
                key=request["agent_key"],
            )
            sender: AgentId | None = None
            if request.get("sender_type"):
                sender = AgentId(
                    type=request["sender_type"],
                    key=request["sender_key"],
                )

            if recipient.type not in self._runtime._handlers:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"No handler for agent type {recipient.type!r}",
                )

            return await self._runtime._dispatch_local(
                request["payload"],
                sender=sender,
                target=recipient,
            )


class GrpcRuntime(BaseRemoteRuntime):
    """gRPC-backed ``AgentRuntime`` for distributed agent dispatch.

    Inherits all local handler management from :class:`BaseRemoteRuntime`
    and adds:
    - A gRPC server with generic JSON handlers (no proto compilation)
    - Remote dispatch via ``grpc.aio`` unary stubs

    Parameters
    ----------
    listen_address:
        Address for the local gRPC server (e.g. ``"0.0.0.0:50051"``).
    remote_peers:
        Mapping of ``agent_type → grpc_address`` for remote dispatch.
    """

    def __init__(
        self,
        listen_address: str = "0.0.0.0:50051",
        remote_peers: Optional[Dict[str, str]] = None,
    ) -> None:
        if not _HAS_GRPC:
            raise ImportError(
                "grpcio is required for GrpcRuntime. "
                "Install with: uv add grpcio grpcio-tools protobuf"
            )
        super().__init__()
        self._listen_address = listen_address
        self._remote_peers = remote_peers or {}
        self._server: Any = None

    # -- Transport lifecycle ------------------------------------------------

    async def start(self) -> None:
        """Start the gRPC server for local agent servicers."""
        self._server = grpc_aio.server()
        self._server.add_generic_rpc_handlers([_AgentServiceHandler(self)])
        self._server.add_insecure_port(self._listen_address)
        await self._server.start()
        self._started = True
        logger.info(
            "GrpcRuntime started (listen=%s, peers=%d, handlers=%s)",
            self._listen_address,
            len(self._remote_peers),
            list(self._handlers.keys()),
        )

    async def stop(self) -> None:
        """Stop the gRPC server."""
        try:
            self._started = False
            if self._server is not None:
                await self._server.stop(grace=5)
        finally:
            # H12 fix: always clear server reference in finally
            self._server = None
            logger.info("GrpcRuntime stopped")

    # -- Remote transport ---------------------------------------------------

    async def _remote_send(
        self,
        message: Any,
        *,
        sender: AgentId | None,
        recipient: AgentId,
    ) -> Any:
        """Make a unary gRPC call to a remote peer for *recipient*.

        Raises ``ValueError`` if no remote peer is configured for the type.
        Raises ``RuntimeError`` if the gRPC call fails.
        """
        if recipient.type not in self._remote_peers:
            raise ValueError(
                f"No local handler or remote peer for agent type {recipient.type!r}"
            )
        return await self._grpc_call(recipient, message, sender)

    async def _grpc_call(
        self,
        recipient: AgentId,
        message: Any,
        sender: AgentId | None,
    ) -> Any:
        """Serialize and dispatch via a gRPC unary stub.

        H8 fix: wraps gRPC errors in ``RuntimeError`` so callers get
        a meaningful exception.
        """
        address = self._remote_peers[recipient.type]
        payload_json = json.dumps(
            {
                "agent_type": recipient.type,
                "agent_key": recipient.key,
                "sender_type": sender.type if sender else None,
                "sender_key": sender.key if sender else None,
                "payload": message,
            }
        )

        try:
            async with grpc_aio.insecure_channel(address) as channel:
                stub = channel.unary_unary(
                    "/raavan.runtime.AgentService/SendMessage",
                    request_serializer=lambda x: x.encode("utf-8"),
                    response_deserializer=lambda x: json.loads(x.decode("utf-8")),
                )
                return await stub(payload_json)
        except Exception as exc:
            raise RuntimeError(
                f"GrpcRuntime: remote call to {recipient} at {address} failed: {exc}"
            ) from exc
