"""Batch processor — concurrent execution of agent or LLM tasks.

Supports:
  • Concurrency control via ``asyncio.Semaphore``
  • Token-bucket rate limiting (requests per second)
  • Per-item retries with exponential backoff + jitter
  • Progress callbacks (``on_item_complete``, ``on_item_error``)
  • Partial result collection — completed items are preserved on failure
  • Works with ``Agent.run()``, ``BaseModelClient.generate()``, or any
    ``async (input) -> output`` callable
"""

from __future__ import annotations
from ravi.logger import setup_logging

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional, Sequence, TypeVar

from ravi.fabric.batch.config import BatchConfig, BatchItem, BatchResult, BatchStatus

logger = setup_logging()

T = TypeVar("T")
R = TypeVar("R")


class BatchProcessor:
    """Execute an async callable concurrently over a list of inputs.

    The callable ``fn`` is invoked once per input item.  The processor
    manages concurrency, rate-limiting, retries, and result collection.

    Parameters:
        fn: Async callable ``(input) -> output``.
        config: ``BatchConfig`` controlling concurrency, retries, etc.

    Example::

        from ravi import Agent
        from ravi.fabric.batch import BatchProcessor, BatchConfig

        agent = Agent(name="Summariser", model="gpt-5-mini")

        async def summarise(text: str) -> str:
            result = await agent.run(f"Summarise: {text}")
            return result.output_text

        processor = BatchProcessor(fn=summarise, config=BatchConfig(max_concurrency=5))
        batch_result = await processor.run(texts)
        for item in batch_result.items:
            print(item.output if item.success else item.error)
    """

    def __init__(
        self,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        config: Optional[BatchConfig] = None,
    ):
        self.fn = fn
        self.config = config or BatchConfig()
        self._cancelled = False

    async def run(self, inputs: Sequence[Any]) -> BatchResult:
        """Process all inputs and return the aggregated result."""
        result = BatchResult(
            total=len(inputs),
            started_at=datetime.now(timezone.utc),
            status=BatchStatus.RUNNING,
        )
        result.items = [BatchItem(index=i, input=inp) for i, inp in enumerate(inputs)]

        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        rate_limiter = (
            _TokenBucket(self.config.rate_limit_rps)
            if self.config.rate_limit_rps
            else None
        )

        tasks = [
            asyncio.create_task(self._process_item(item, semaphore, rate_limiter))
            for item in result.items
        ]

        # Wait for all — even if some fail
        await asyncio.gather(*tasks, return_exceptions=True)

        # Compute aggregates
        result.succeeded = sum(1 for item in result.items if item.success)
        result.failed = result.total - result.succeeded
        result.completed_at = datetime.now(timezone.utc)
        if result.started_at is not None:
            result.duration_ms = (
                result.completed_at - result.started_at
            ).total_seconds() * 1000

        if self._cancelled:
            result.status = BatchStatus.CANCELLED
        elif result.failed == 0:
            result.status = BatchStatus.COMPLETED
        elif result.succeeded == 0:
            result.status = BatchStatus.FAILED
        else:
            result.status = BatchStatus.PARTIAL

        logger.info(result.summary())
        return result

    def cancel(self) -> None:
        """Signal the batch to stop processing new items."""
        self._cancelled = True

    async def _process_item(
        self,
        item: BatchItem,
        semaphore: asyncio.Semaphore,
        rate_limiter: Optional[_TokenBucket],
    ) -> None:
        """Process a single item with retries, concurrency, and rate-limiting."""
        if self._cancelled:
            item.error = "Batch cancelled"
            return

        async with semaphore:
            last_error: Optional[Exception] = None
            max_attempts = 1 + self.config.max_retries

            for attempt in range(1, max_attempts + 1):
                if self._cancelled:
                    item.error = "Batch cancelled"
                    return

                item.attempts = attempt

                # Rate limiting
                if rate_limiter:
                    await rate_limiter.acquire()

                start = time.perf_counter()
                try:
                    if self.config.timeout_per_item:
                        output = await asyncio.wait_for(
                            self.fn(item.input),
                            timeout=self.config.timeout_per_item,
                        )
                    else:
                        output = await self.fn(item.input)

                    elapsed = (time.perf_counter() - start) * 1000
                    item.output = output
                    item.success = True
                    item.duration_ms = elapsed

                    if self.config.on_item_complete:
                        await self.config.on_item_complete(item.index, item)

                    return  # Success — done with this item

                except Exception as exc:
                    elapsed = (time.perf_counter() - start) * 1000
                    item.duration_ms = elapsed
                    last_error = exc

                    if attempt < max_attempts:
                        delay = min(
                            self.config.retry_base_delay * (2 ** (attempt - 1)),
                            self.config.retry_max_delay,
                        )
                        jitter = random.uniform(0, delay * 0.25)  # noqa: S311
                        logger.warning(
                            "Batch item %d attempt %d/%d failed: %s — "
                            "retrying in %.1fs",
                            item.index,
                            attempt,
                            max_attempts,
                            exc,
                            delay + jitter,
                        )
                        await asyncio.sleep(delay + jitter)
                    else:
                        logger.error(
                            "Batch item %d failed after %d attempts: %s",
                            item.index,
                            max_attempts,
                            exc,
                        )

            # All retries exhausted
            item.error = str(last_error) if last_error else "Unknown error"

            if self.config.on_item_error and last_error:
                await self.config.on_item_error(item.index, last_error)

            if not self.config.continue_on_error:
                self._cancelled = True


class _TokenBucket:
    """Simple async token-bucket rate limiter.

    Limits throughput to ``rate`` requests per second by tracking the
    next allowed timestamp and sleeping if the caller is too fast.
    """

    def __init__(self, rate: float):
        self._interval = 1.0 / rate
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + self._interval
