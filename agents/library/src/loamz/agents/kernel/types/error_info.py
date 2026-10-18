"""ErrorInfo — a failure as data.

Failures cross process boundaries constantly: a child run's error reaches its
parent, a dead-lettered message keeps its last error, a journaled effect records
why it failed, a tool result reports a failure to the model. Each of those used
to carry a bare string (five different places), which loses the one thing a
receiver needs to decide what to do — whether the failure is worth retrying and
what kind it was.

``code`` is a stable, machine-readable identifier (``rate_limited``,
``context_length``, ``tool_timeout``) so dashboards and alerts group failures by
meaning instead of by Python class name.
"""

from __future__ import annotations

from pydantic import Field

from loamz.agents.kernel.types.content import JsonObject, KernelModel
from loamz.data.storage.errors import LoamzError


class ErrorInfo(KernelModel):
    code: str
    message: str
    retryable: bool = False
    details: JsonObject = Field(default_factory=dict)

    @classmethod
    def of(cls, error: LoamzError) -> ErrorInfo:
        """A typed failure as data, to journal or hand to another process."""
        return cls(code=error.code, message=str(error), retryable=error.retryable)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


__all__ = ["ErrorInfo"]
