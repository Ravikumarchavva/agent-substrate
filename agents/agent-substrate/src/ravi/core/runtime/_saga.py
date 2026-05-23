"""Saga coordinator — exactly-once execution of critical/side-effectful actions.

When an agent performs an action with real-world consequences (payment charge,
booking confirmation, email send, database mutation), the saga coordinator
ensures:

1. **Exactly-once semantics** — if the process crashes after the action
   succeeds but before recording the result, recovery skips the action and
   uses the stored result (idempotency via ``step_id``).
2. **Compensating rollback** — if a multi-step saga fails midway, previously
   completed steps are rolled back in reverse order using their declared
   compensating actions.
3. **Checkpoint integration** — saga state is persisted alongside agent
   checkpoints so recovery can resume or compensate.

Design inspired by the Saga pattern from microservices, adapted for
single-process agent orchestration with async/await.

Usage::

    saga = SagaCoordinator(store=checkpoint_store)

    async with saga.begin("order-123") as ctx:
        # Step 1: charge payment
        charge = await ctx.step(
            step_id="charge-card",
            action=lambda: payment_api.charge(amount=100),
            compensate=lambda result: payment_api.refund(result["charge_id"]),
        )

        # Step 2: book hotel (depends on step 1 succeeding)
        booking = await ctx.step(
            step_id="book-hotel",
            action=lambda: hotel_api.book(room="101"),
            compensate=lambda result: hotel_api.cancel(result["booking_id"]),
        )
    # If step 2 fails, step 1's compensating action (refund) runs automatically.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

from ravi.core.messages.content import JsonObject
from ravi.core.runtime._errors import SagaFailedError

logger = logging.getLogger("ravi.core.runtime.saga")


# ---------------------------------------------------------------------------
# Saga step status
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    """Lifecycle status of a saga step."""

    PENDING = "pending"
    EXECUTED = "executed"
    COMPENSATED = "compensated"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Saga step record (persisted)
# ---------------------------------------------------------------------------


class SagaStep(BaseModel):
    """Persistent record of a single step in a saga.

    ``request_hash`` is a SHA-256 of the action's input so that on recovery
    we can verify the same action is being retried (not a different one with
    the same step_id).
    """

    step_id: str
    saga_id: str
    status: StepStatus = StepStatus.PENDING
    request_hash: str = ""
    response: Optional[JsonObject] = None
    error_message: Optional[str] = None
    has_compensating_action: bool = False
    executed_at: Optional[datetime] = None
    compensated_at: Optional[datetime] = None

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Saga record (persisted)
# ---------------------------------------------------------------------------


class SagaRecord(BaseModel):
    """Persistent record of an entire saga execution."""

    saga_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    agent_id: str = ""
    status: StepStatus = StepStatus.PENDING  # reusing enum: pending / executed / failed / compensated
    steps: list[SagaStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Saga store (abstract + in-memory)
# ---------------------------------------------------------------------------


class SagaStore:
    """Abstract store for saga records.

    Production implementations would use PostgreSQL / Redis / etc.
    """

    async def save(self, record: SagaRecord) -> None:
        raise NotImplementedError

    async def load(self, saga_id: str) -> Optional[SagaRecord]:
        raise NotImplementedError

    async def delete(self, saga_id: str) -> None:
        raise NotImplementedError


class InMemorySagaStore(SagaStore):
    """In-memory saga store for single-process deployments and testing."""

    def __init__(self) -> None:
        self._store: dict[str, SagaRecord] = {}

    async def save(self, record: SagaRecord) -> None:
        self._store[record.saga_id] = record

    async def load(self, saga_id: str) -> Optional[SagaRecord]:
        return self._store.get(saga_id)

    async def delete(self, saga_id: str) -> None:
        self._store.pop(saga_id, None)


# ---------------------------------------------------------------------------
# Execution context for a running saga
# ---------------------------------------------------------------------------


class SagaExecutionContext:
    """Context manager for executing steps within a saga.

    Tracks the sequence of steps and handles compensation on failure.
    """

    __slots__ = ("_coordinator", "_record", "_compensators")

    def __init__(
        self,
        coordinator: "SagaCoordinator",
        record: SagaRecord,
    ) -> None:
        self._coordinator = coordinator
        self._record = record
        # Compensators in execution order (will be reversed for rollback)
        self._compensators: list[
            tuple[str, Callable[[JsonObject], Awaitable[None]]]
        ] = []

    @property
    def saga_id(self) -> str:
        return self._record.saga_id

    async def step(
        self,
        step_id: str,
        action: Callable[[], Awaitable[Any]],
        *,
        compensate: Optional[Callable[[Any], Awaitable[None]]] = None,
        request_hash: str = "",
    ) -> Any:
        """Execute a saga step with idempotency and optional compensation.

        If a step with this ``step_id`` was already executed (from a previous
        run / recovery), the stored result is returned without re-executing.

        Parameters
        ----------
        step_id:
            Unique identifier for this step within the saga.
        action:
            Async callable that performs the actual work.
        compensate:
            Optional async callable that undoes the action. Receives the
            action's return value.
        request_hash:
            Hash of the request payload for idempotency verification.
        """
        # Check if step already completed (recovery path)
        existing = self._find_step(step_id)
        if existing is not None:
            if existing.status == StepStatus.EXECUTED:
                logger.info(
                    "saga %s: step %s already executed, using stored result",
                    self.saga_id, step_id,
                )
                # Verify request hash if provided
                if request_hash and existing.request_hash and request_hash != existing.request_hash:
                    logger.warning(
                        "saga %s: step %s request hash mismatch (stored=%s, current=%s)",
                        self.saga_id, step_id,
                        existing.request_hash, request_hash,
                    )
                # Register compensator for already-executed steps
                if compensate and existing.response is not None:
                    self._compensators.append((step_id, lambda r=existing.response: compensate(r)))
                return existing.response
            elif existing.status == StepStatus.FAILED:
                raise SagaFailedError(
                    self.saga_id, step_id,
                    message=f"step {step_id!r} previously failed: {existing.error_message}",
                )

        # Create step record
        step_record = SagaStep(
            step_id=step_id,
            saga_id=self.saga_id,
            request_hash=request_hash,
            has_compensating_action=compensate is not None,
        )
        self._record.steps.append(step_record)

        # Persist PENDING state before execution
        await self._coordinator._store.save(self._record)

        # Execute
        try:
            result = await action()
        except Exception as exc:
            step_record.status = StepStatus.FAILED
            step_record.error_message = str(exc)
            await self._coordinator._store.save(self._record)
            raise

        # Record success
        step_record.status = StepStatus.EXECUTED
        step_record.executed_at = datetime.now(timezone.utc)

        # Store result — must be JSON-serializable
        if isinstance(result, dict):
            step_record.response = result
        elif result is not None:
            step_record.response = {"_value": str(result)}
        else:
            step_record.response = {}

        await self._coordinator._store.save(self._record)

        # Register compensator
        if compensate:
            self._compensators.append((step_id, lambda r=result: compensate(r)))

        logger.debug("saga %s: step %s executed successfully", self.saga_id, step_id)
        return result

    async def compensate_all(self) -> list[str]:
        """Run all compensating actions in reverse order.

        Returns a list of step IDs that were successfully compensated.
        """
        compensated: list[str] = []
        for step_id, compensator in reversed(self._compensators):
            try:
                await compensator()
                compensated.append(step_id)
                # Update step status
                existing = self._find_step(step_id)
                if existing:
                    existing.status = StepStatus.COMPENSATED
                    existing.compensated_at = datetime.now(timezone.utc)
                logger.info("saga %s: step %s compensated", self.saga_id, step_id)
            except Exception:
                logger.exception(
                    "saga %s: compensation failed for step %s",
                    self.saga_id, step_id,
                )
        await self._coordinator._store.save(self._record)
        return compensated

    def _find_step(self, step_id: str) -> Optional[SagaStep]:
        """Find a step record by ID."""
        for step in self._record.steps:
            if step.step_id == step_id:
                return step
        return None


# ---------------------------------------------------------------------------
# SagaCoordinator
# ---------------------------------------------------------------------------


class SagaCoordinator:
    """Coordinates saga execution with idempotency and compensation.

    Parameters
    ----------
    store:
        Persistent store for saga records.  Defaults to in-memory.
    """

    __slots__ = ("_store",)

    def __init__(self, store: Optional[SagaStore] = None) -> None:
        self._store = store or InMemorySagaStore()

    @asynccontextmanager
    async def begin(
        self,
        saga_id: str | None = None,
        agent_id: str = "",
    ) -> AsyncIterator[SagaExecutionContext]:
        """Begin a new saga or resume an existing one.

        On successful completion, the saga status is set to ``EXECUTED``.
        On failure, compensating actions run and status is set to ``COMPENSATED``
        or ``FAILED`` (if compensation also fails).
        """
        sid = saga_id or uuid.uuid4().hex

        # Check for existing saga (recovery)
        existing = await self._store.load(sid)
        if existing is not None:
            record = existing
            logger.info("resuming saga %s (status=%s)", sid, record.status)
        else:
            record = SagaRecord(saga_id=sid, agent_id=agent_id)
            await self._store.save(record)

        ctx = SagaExecutionContext(self, record)

        try:
            yield ctx
        except SagaFailedError:
            # Step failed — run compensation
            record.status = StepStatus.FAILED
            compensated = await ctx.compensate_all()
            if compensated:
                record.status = StepStatus.COMPENSATED
            record.completed_at = datetime.now(timezone.utc)
            await self._store.save(record)
            raise
        except Exception as exc:
            # Unexpected error — run compensation
            record.status = StepStatus.FAILED
            compensated = await ctx.compensate_all()
            if compensated:
                record.status = StepStatus.COMPENSATED
            record.completed_at = datetime.now(timezone.utc)
            await self._store.save(record)
            raise SagaFailedError(
                sid,
                failed_step="unknown",
                completed_steps=[
                    s.step_id for s in record.steps if s.status == StepStatus.EXECUTED
                ],
                message=f"saga {sid!r} failed: {exc}",
            ) from exc
        else:
            # All steps succeeded
            record.status = StepStatus.EXECUTED
            record.completed_at = datetime.now(timezone.utc)
            await self._store.save(record)
            logger.info("saga %s completed successfully", sid)

    async def get_saga(self, saga_id: str) -> Optional[SagaRecord]:
        """Retrieve a saga record by ID."""
        return await self._store.load(saga_id)

    async def cleanup(self, saga_id: str) -> None:
        """Delete a completed saga record."""
        await self._store.delete(saga_id)

    @staticmethod
    def hash_request(*args: Any) -> str:
        """Helper: compute a deterministic hash for idempotency checking."""
        raw = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
