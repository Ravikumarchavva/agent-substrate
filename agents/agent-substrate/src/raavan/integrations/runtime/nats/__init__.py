"""NATS runtime backend.

Provides ``NATSBridge`` — a pub/sub streaming bridge backed by NATS
JetStream for durable event delivery.  Useful for streaming agent events
across processes / pods.

Requires: ``nats-py``.
"""

from __future__ import annotations

from raavan.integrations.runtime.nats.bridge import NATSBridge

__all__ = ["NATSBridge"]
