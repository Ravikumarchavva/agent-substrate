"""Execution-layer error types."""

from __future__ import annotations


class MaxAgentDepthError(RuntimeError):
    """Raised when child_context() would exceed max_agent_depth."""


class AgentTimeoutError(TimeoutError):
    """Raised when an agent run exceeds its configured deadline."""


class CircuitOpenError(RuntimeError):
    """Raised by CircuitBreaker.allow_request() when the circuit is open."""
