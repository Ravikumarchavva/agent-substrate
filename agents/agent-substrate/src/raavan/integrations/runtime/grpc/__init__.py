"""gRPC runtime backend.

Provides ``GrpcRuntime`` — an ``AgentRuntime`` implementation that routes
agent messages over gRPC.  Enables distributing agents across multiple
processes or machines.

Requires: ``grpcio``, ``grpcio-tools``, ``protobuf``.
"""

from __future__ import annotations

from raavan.integrations.runtime.grpc.runtime import GrpcRuntime

__all__ = ["GrpcRuntime"]
