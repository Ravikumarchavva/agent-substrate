"""Tests verifying the unified exception hierarchy."""

from __future__ import annotations


from substrate.exceptions import (
    AgentError,
    ConfigurationError,
    ContextLimitExceededError,
    ModelProviderError,
    ToolError,
    ToolNotFoundError,
)
from substrate.kernel.exceptions import (
    KernelError,
    PermanentError,
    SuspendInterrupt,
)


def test_public_exceptions_inherit_from_kernel_error() -> None:
    """Every AgentError must be an instance of KernelError."""
    err = AgentError("something failed")
    assert isinstance(err, KernelError)
    assert isinstance(ConfigurationError("missing key"), KernelError)
    assert isinstance(ModelProviderError("rate limit"), KernelError)
    assert isinstance(ToolError("fail", tool_name="search"), KernelError)
    assert isinstance(ToolNotFoundError("missing", tool_name="search"), KernelError)
    assert isinstance(ContextLimitExceededError("too long"), KernelError)


def test_deterministic_errors_inherit_from_permanent_error() -> None:
    """Non-retryable deterministic errors must inherit from PermanentError.

    This ensures the Worker's retry policy immediately skips futile retries
    on configuration mistakes, missing tools, or context window overflow.
    """
    assert issubclass(ConfigurationError, PermanentError)
    assert issubclass(ToolNotFoundError, PermanentError)
    assert issubclass(ContextLimitExceededError, PermanentError)


def test_suspend_interrupt_is_base_exception() -> None:
    """SuspendInterrupt must be a BaseException, not an Exception.

    This prevents user try...except Exception blocks from silently swallowing
    dormancy signals.
    """
    assert issubclass(SuspendInterrupt, BaseException)
    assert not issubclass(SuspendInterrupt, Exception)


def test_kernel_exceptions_module() -> None:
    """substrate.kernel.exceptions must export all L0 exceptions with correct semantic tiering."""
    import substrate.kernel.exceptions as ke

    # 1. Control Signals (BaseException)
    assert issubclass(ke.ControlSignal, BaseException)
    assert not issubclass(ke.ControlSignal, Exception)
    assert issubclass(ke.SuspendInterrupt, ke.ControlSignal)
    assert issubclass(ke.CancellationError, ke.ControlSignal)

    # 2. Root Kernel Error
    assert issubclass(ke.KernelError, Exception)

    # 3. Transient / Retryable Errors
    assert issubclass(ke.TransientError, ke.KernelError)
    assert issubclass(ke.ConcurrentAppendError, ke.TransientError)
    assert issubclass(ke.ThreadBusyError, ke.TransientError)

    # 4. Permanent / Fatal Errors
    assert issubclass(ke.PermanentError, ke.KernelError)
    assert issubclass(ke.AgentCrashError, ke.PermanentError)
    assert issubclass(ke.BlockValidationError, ke.PermanentError)
    assert issubclass(ke.BlockValidationError, ValueError)

    # 5. Policy Terminations
    assert issubclass(ke.PolicyTermination, ke.KernelError)
    assert issubclass(ke.BudgetExhaustedError, ke.PolicyTermination)
    assert issubclass(ke.MiddlewareTermination, ke.PolicyTermination)

