"""Tests verifying the unified exception hierarchy."""

from __future__ import annotations


from substrate.types import (
    ContextLengthError,
    KernelError,
    PermanentError,
    SuspendInterrupt,
)
from substrate.types.errors import ToolDeclarationError, UnroutableMessageError


def test_deterministic_errors_inherit_from_permanent_error() -> None:
    """Errors that retrying cannot fix must be PermanentError, so the worker skips futile retries."""
    for cls in (ContextLengthError, ToolDeclarationError, UnroutableMessageError):
        assert issubclass(cls, PermanentError) and issubclass(cls, KernelError)


def test_suspend_interrupt_is_base_exception() -> None:
    """SuspendInterrupt must be a BaseException, not an Exception.

    This prevents user try...except Exception blocks from silently swallowing
    dormancy signals.
    """
    assert issubclass(SuspendInterrupt, BaseException)
    assert not issubclass(SuspendInterrupt, Exception)


def test_kernel_exceptions_module() -> None:
    """substrate.types.errors must export all L0 exceptions with correct semantic tiering."""
    import substrate.types.errors as ke

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
