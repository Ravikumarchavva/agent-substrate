"""Threshold-based checkpoint generation strategy for post-turn compaction."""

from __future__ import annotations

from typing import Callable, Optional
from uuid import uuid4

from substrate.kernel.agent.context import CompactionContext
from substrate.kernel.storage.history import HistoryCheckpoint
from substrate.agents.context.builder import _estimate_total_tokens


class ThresholdCheckpointStrategy:
    """Produces a HistoryCheckpoint proposal when context exceeds a turn or token threshold.

    Parameters:
        turn_threshold: Number of messages in context that triggers a checkpoint.
        token_threshold: Total estimated tokens in context that triggers a checkpoint.
        summary_builder: Optional custom callable to generate the checkpoint summary string.
    """

    def __init__(
        self,
        *,
        turn_threshold: Optional[int] = None,
        token_threshold: Optional[int] = None,
        summary_builder: Optional[Callable[[CompactionContext], str]] = None,
    ) -> None:
        self.turn_threshold = turn_threshold
        self.token_threshold = token_threshold
        self.summary_builder = summary_builder

    async def create_checkpoint(
        self, context: CompactionContext
    ) -> HistoryCheckpoint | None:
        if not context.leaf_node_id:
            return None

        turn_count = len(context.messages)
        should_compact = False

        if self.turn_threshold is not None and turn_count >= self.turn_threshold:
            should_compact = True

        if not should_compact and self.token_threshold is not None:
            total_tokens = _estimate_total_tokens(context.messages)
            if total_tokens >= self.token_threshold:
                should_compact = True

        if not should_compact:
            return None

        # Build summary
        if self.summary_builder is not None:
            summary_text = self.summary_builder(context)
        else:
            summary_text = (
                f"Auto-generated checkpoint at turn {turn_count} covering "
                f"{turn_count} messages up to node '{context.leaf_node_id}'."
            )

        parent_id = (
            context.existing_checkpoint.id if context.existing_checkpoint else None
        )

        return HistoryCheckpoint(
            id=uuid4().hex,
            session_id=context.session_id,
            anchor_message_id=context.leaf_node_id,
            summary=summary_text,
            parent_checkpoint_id=parent_id,
        )


__all__ = ["ThresholdCheckpointStrategy"]

