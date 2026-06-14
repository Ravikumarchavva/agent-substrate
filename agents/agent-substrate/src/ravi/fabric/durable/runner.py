"""DurableRunner — SequentialFlow wrapper with per-step checkpointing.

After each step completes, the runner saves a FlowCheckpoint recording the
last completed step index.  On resume it reloads that checkpoint and skips
already-completed steps, re-feeding the saved accumulated output into the
first remaining step.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from ravi.agents.middleware import AgentRunResult
from ravi.fabric.durable.checkpoint import FlowCheckpoint
from ravi.fabric.durable.store import CheckpointStore, InMemoryCheckpointStore
from ravi.fabric.flows.agent import SequentialFlow

logger = logging.getLogger(__name__)


class DurableRunner:
    """Wraps a SequentialFlow with checkpoint-based resumability.

    Parameters
    ----------
    flow:  The SequentialFlow to execute.
    store: CheckpointStore backend.  Defaults to InMemoryCheckpointStore.
    """

    def __init__(
        self,
        flow: SequentialFlow,
        *,
        store: Optional[CheckpointStore] = None,
    ) -> None:
        self._flow = flow
        self._store: CheckpointStore = store or InMemoryCheckpointStore()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        input_text: str,
        *,
        run_id: Optional[str] = None,
        resume: bool = False,
        **kwargs,
    ) -> AgentRunResult:
        """Execute the flow, checkpointing after each step.

        Parameters
        ----------
        input_text: Initial user input fed to the first step.
        run_id:     Stable ID for this logical run (auto-generated if None).
        resume:     If True, load any existing checkpoint and skip ahead.
        """
        run_id = run_id or str(uuid4())
        flow_id = self._flow.name

        # --- Determine start state ---
        start_index = 0
        accumulated = input_text

        if resume:
            cp = await self._store.load(run_id, flow_id)
            if cp is not None:
                start_index = cp.step_index + 1  # skip to next step
                accumulated = cp.state.get("accumulated", input_text)
                logger.info(
                    "[DurableRunner] Resuming run_id=%s from step %d",
                    run_id,
                    start_index,
                )

        last_result: AgentRunResult | None = None

        for idx, step in enumerate(self._flow.steps):
            if idx < start_index:
                continue  # already completed in a prior run

            logger.debug(
                "[DurableRunner] run_id=%s executing step %d/%d: %s",
                run_id,
                idx + 1,
                len(self._flow.steps),
                getattr(step, "name", repr(step)),
            )

            last_result = await step.run(accumulated, **kwargs)

            if last_result.output:
                accumulated = f"{accumulated}\n\n{last_result.output}"

            # Persist checkpoint after the step completes
            cp = FlowCheckpoint(
                run_id=run_id,
                flow_id=flow_id,
                step_index=idx,
                state={"accumulated": accumulated},
                resumed_at=datetime.now() if resume and idx == start_index else None,
            )
            await self._store.save(cp)

        if last_result is None:
            return AgentRunResult(output="", status="error", run_id=run_id)

        return AgentRunResult(
            output=last_result.output,
            status=last_result.status,
            tool_calls=last_result.tool_calls,
            run_id=run_id,
        )

    async def resume(
        self,
        run_id: str,
        input_text: str = "",
        **kwargs,
    ) -> AgentRunResult:
        """Convenience wrapper: resume an interrupted run by its run_id."""
        return await self.run(input_text, run_id=run_id, resume=True, **kwargs)
