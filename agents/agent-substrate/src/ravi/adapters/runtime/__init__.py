"""Runtime integration backends — pluggable ``AgentRuntime`` implementations.

Each sub-package provides an ``AgentRuntime`` backend for a different
infrastructure. All backends conform to the same protocol defined
in ``ravi.kernel.runtime`` and inherit from ``BaseRuntime`` (also in
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

    from ravi.adapters.runtime import BaseRemoteRuntime
    from ravi.adapters.runtime.grpc import GrpcRuntime
    from ravi.adapters.runtime.restate import RestateRuntime
    from ravi.adapters.runtime.nats import NATSBridge
"""

from __future__ import annotations

from ravi.adapters.runtime._base import BaseRemoteRuntime

__all__ = ["BaseRemoteRuntime"]
