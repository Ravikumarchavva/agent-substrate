"""Runtime errors — single source of truth for message-delivery failures."""

from __future__ import annotations


class AgentNotFoundError(Exception):
    """Raised when sending to an AgentId that has no registered handler."""


class HandlerError(Exception):
    """Raised when a message handler raises an exception.

    Wraps the original exception so callers of ``send_message`` receive a
    typed error rather than a bare exception or silent ``None``.
    """


__all__ = ["AgentNotFoundError", "HandlerError"]
