"""Runtime integration backends — pluggable ``AgentRuntime`` implementations.

Each sub-package provides an ``AgentRuntime`` backend for a different
infrastructure. All backends conform to the same protocol defined
in ``raavan.core.runtime`` and inherit from ``BaseRuntime`` (also in
``core/runtime``).

Available backends:

- **grpc** — gRPC-based remote agent dispatch (``GrpcRuntime``)
- **restate** — Restate durable workflow engine (``RestateRuntime``)
- **nats** — NATS JetStream pub/sub streaming (``NATSBridge``)

All remote backends inherit from ``BaseRemoteRuntime`` which extends
``BaseRuntime`` with local dispatch helpers and the ``_remote_send``
abstract method for transport-specific delivery.

Inheritance hierarchy::

    BaseRuntime (ABC, core/runtime)
    ├── LocalRuntime (core/runtime)
    └── BaseRemoteRuntime (integrations/runtime)
        ├── GrpcRuntime
        └── RestateRuntime

Usage::

    from raavan.integrations.runtime import BaseRemoteRuntime
    from raavan.integrations.runtime.grpc import GrpcRuntime
    from raavan.integrations.runtime.restate import RestateRuntime
    from raavan.integrations.runtime.nats import NATSBridge
"""

from __future__ import annotations

from raavan.integrations.runtime._base import BaseRemoteRuntime

__all__ = ["BaseRemoteRuntime"]
