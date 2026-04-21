"""Batch processing data models — config, per-item result, and aggregate result."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Optional


class BatchStatus(str, Enum):
    """Lifecycle states for a batch job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"  # some items failed
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BatchConfig:
    """Configuration for a batch processing run.

    Attributes:
        max_concurrency: Maximum parallel items at once (asyncio.Semaphore).
        max_retries: Per-item retry attempts.
        retry_base_delay: Seconds before first retry (exponential backoff).
        retry_max_delay: Cap on retry delay.
        timeout_per_item: Seconds to allow per item (``None`` = no timeout).
        rate_limit_rps: Max requests per second (``None`` = unlimited).
        continue_on_error: If ``True``, failed items don't abort the batch.
        on_item_complete: Optional async callback ``(index, item_result) -> None``.
        on_item_error: Optional async callback ``(index, error) -> None``.
    """

    max_concurrency: int = 10
    max_retries: int = 2
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0
    timeout_per_item: Optional[float] = 120.0
    rate_limit_rps: Optional[float] = None
    continue_on_error: bool = True
    on_item_complete: Optional[
        Callable[[int, "BatchItem"], Coroutine[Any, Any, None]]
    ] = None
    on_item_error: Optional[Callable[[int, Exception], Coroutine[Any, Any, None]]] = (
        None
    )


@dataclass
class BatchItem:
    """Result for a single item in the batch.

    Attributes:
        index: Zero-based position in the input list.
        input: The original input value.
        output: The result if successful.
        error: Error message if the item failed.
        success: Whether processing succeeded.
        attempts: Number of attempts (1 = first try, 2+ = retries).
        duration_ms: Wall-clock time for this item.
    """

    index: int
    input: Any
    output: Any = None
    error: Optional[str] = None
    success: bool = False
    attempts: int = 0
    duration_ms: Optional[float] = None


@dataclass
class BatchResult:
    """Aggregate result of a batch processing run.

    Attributes:
        batch_id: Unique identifier for this batch run.
        status: Overall batch status.
        items: Per-item results (same order as input).
        total: Total number of items.
        succeeded: Count of successful items.
        failed: Count of failed items.
        duration_ms: Total wall-clock time.
        started_at: UTC timestamp when batch started.
        completed_at: UTC timestamp when batch finished.
    """

    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: BatchStatus = BatchStatus.PENDING
    items: list[BatchItem] = field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    duration_ms: Optional[float] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        """Fraction of items that succeeded (0.0–1.0)."""
        return self.succeeded / self.total if self.total > 0 else 0.0

    def summary(self) -> str:
        """One-line human-readable summary."""
        return (
            f"Batch {self.batch_id[:8]}… — {self.status.value}: "
            f"{self.succeeded}/{self.total} succeeded "
            f"({self.success_rate:.0%}) in {self.duration_ms:.0f}ms"
            if self.duration_ms
            else f"Batch {self.batch_id[:8]}… — {self.status.value}: "
            f"{self.succeeded}/{self.total} succeeded ({self.success_rate:.0%})"
        )
