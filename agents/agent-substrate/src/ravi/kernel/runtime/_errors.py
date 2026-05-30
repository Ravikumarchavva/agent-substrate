"""Runtime message-delivery errors — single source of truth.

Only the two errors intrinsic to delivering a message live here. Coordination
concerns (locks, sagas, leases, supervision, backpressure) are not part of the
in-process message runtime; if a backend needs them it defines its own.
"""

from __future__ import annotations


class AgentNotFoundError(Exception):
    """Raised when sending to an ``AgentId`` that has no registered handler."""


class HandlerError(Exception):
    """Raised when a message handler crashes.

    Wraps the original exception so callers of ``send_message`` receive a
    proper error instead of a silent ``None``.
    """


__all__ = ["AgentNotFoundError", "HandlerError"]
