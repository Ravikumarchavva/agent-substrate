"""Tests verifying the unified exception hierarchy."""

from __future__ import annotations

from loamz.engine.agents.kernel.errors import (
    ContextLengthError,
    SuspendInterrupt,
    ToolDeclarationError,
    UnroutableMessageError,
)
from loamz.engine.errors import LoamzError, PermanentError


def test_deterministic_errors_inherit_from_permanent_error() -> None:
    """Errors that retrying cannot fix must be PermanentError, so the worker skips futile retries."""
    for cls in (ContextLengthError, ToolDeclarationError, UnroutableMessageError):
        assert issubclass(cls, PermanentError) and issubclass(cls, LoamzError)


def test_suspend_interrupt_is_base_exception() -> None:
    """SuspendInterrupt must be a BaseException, not an Exception.

    This prevents user try...except Exception blocks from silently swallowing
    dormancy signals.
    """
    assert issubclass(SuspendInterrupt, BaseException)
    assert not issubclass(SuspendInterrupt, Exception)


def test_kernel_exceptions_module() -> None:
    """loamz.engine.agents.kernel.errors must export all L0 exceptions with correct semantic tiering."""
    from types import SimpleNamespace

    import loamz.engine.agents.kernel.errors as agent_errors
    import loamz.engine.errors as storage_errors

    ke = SimpleNamespace(
        **{
            k: v
            for m in (storage_errors, agent_errors)
            for k, v in vars(m).items()
            if not k.startswith("_")
        }
    )

    # 1. Control Signals (BaseException)
    assert issubclass(ke.ControlSignal, BaseException)
    assert not issubclass(ke.ControlSignal, Exception)
    assert issubclass(ke.SuspendInterrupt, ke.ControlSignal)
    assert issubclass(ke.CancellationError, ke.ControlSignal)

    # 2. Root Kernel Error
    assert issubclass(ke.LoamzError, Exception)

    # 3. Transient / Retryable Errors
    assert issubclass(ke.TransientError, ke.LoamzError)
    assert issubclass(ke.ConcurrentAppendError, ke.TransientError)
    assert issubclass(ke.ThreadBusyError, ke.TransientError)

    # 4. Permanent / Fatal Errors
    assert issubclass(ke.PermanentError, ke.LoamzError)
    assert issubclass(ke.AgentCrashError, ke.PermanentError)
    assert issubclass(ke.BlockValidationError, ke.PermanentError)
    assert issubclass(ke.BlockValidationError, ValueError)

    # 5. Policy Terminations
    assert issubclass(ke.PolicyTermination, ke.LoamzError)
    assert issubclass(ke.BudgetExhaustedError, ke.PolicyTermination)
    assert issubclass(ke.MiddlewareTermination, ke.PolicyTermination)
