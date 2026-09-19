from __future__ import annotations

from substrate.kernel.exceptions import (
    BudgetExhaustedError,
    ConcurrentAppendError,
    KernelError,
    MiddlewareTermination,
    PermanentError,
    SuspendInterrupt,
    ThreadBusyError,
)


class AgentError(KernelError):
    """Base exception for all errors in the Agent Framework.

    Subclasses ``KernelError`` so that catching ``KernelError`` guarantees
    intercepting all framework-level exceptions.
    """

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(AgentError, PermanentError):
    """Raised when there is a configuration issue (e.g. missing API keys).

    Inherits from ``PermanentError`` so the worker skips retries immediately.
    """


class ModelProviderError(AgentError):
    """Raised when the LLM provider fails (e.g. API error, rate limit)."""


class ContextLimitExceededError(ModelProviderError, PermanentError):
    """Raised when the prompt exceeds the context window.

    Inherits from ``PermanentError`` because identical context cannot fit on retry.
    """


class ToolError(AgentError):
    """Base class for tool-related errors."""

    def __init__(self, message: str, tool_name: str, details: dict | None = None) -> None:
        super().__init__(message, details)
        self.tool_name = tool_name


class ToolNotFoundError(ToolError, PermanentError):
    """Raised when a requested tool is not found.

    Inherits from ``PermanentError`` because a non-existent tool cannot succeed on retry.
    """


class ToolExecutionError(ToolError):
    """Raised when a tool fails to execute."""


class AgentExecutionError(AgentError):
    """Raised when the agent fails to complete its run loop."""


__all__ = [
    "KernelError",
    "PermanentError",
    "SuspendInterrupt",
    "BudgetExhaustedError",
    "MiddlewareTermination",
    "ThreadBusyError",
    "ConcurrentAppendError",
    "AgentError",
    "ConfigurationError",
    "ModelProviderError",
    "ContextLimitExceededError",
    "ToolError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "AgentExecutionError",
]
