"""DefaultContextBuilder — constructs token-budgeted prompt windows from resolved DAG nodes."""

from __future__ import annotations

from collections.abc import Sequence

from substrate.kernel.agent.context import ContextBuilder, ContextWindow
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.storage.history import HistoryCheckpoint, MessageNode

_DEFAULT_CHARS_PER_TOKEN = 4.0


def _estimate_message_tokens(msg: ChatMessage, cpt: float = _DEFAULT_CHARS_PER_TOKEN) -> int:
    """Estimate token count for a single message."""
    chars = len(msg.role) + len(msg.text)
    for block in msg.content:
        chars += len(str(block))
    return max(1, int(chars / cpt))


def _estimate_total_tokens(messages: Sequence[ChatMessage], cpt: float = _DEFAULT_CHARS_PER_TOKEN) -> int:
    return sum(_estimate_message_tokens(m, cpt) for m in messages)


class DefaultContextBuilder(ContextBuilder):
    """Reference implementation of ContextBuilder.

    Features:
    - Extracts ChatMessage payloads from MessageNode sequence in chronological order.
    - Splices checkpoint summaries and delta nodes when a checkpoint is provided.
    - Prepends system_instruction (if provided and not already present).
    - Preserves tool call invariants: never severs a tool_use from its tool_result.
    - Applies deterministic sliding-window reduction when token_budget is exceeded.
    """

    def __init__(self, chars_per_token: float = _DEFAULT_CHARS_PER_TOKEN) -> None:
        self._cpt = chars_per_token

    async def build(
        self,
        nodes: Sequence[MessageNode],
        *,
        checkpoint: HistoryCheckpoint | None = None,
        token_budget: int | None = None,
        system_instruction: str | None = None,
    ) -> ContextWindow:
        checkpoint_id: str | None = None
        working_nodes = list(nodes)

        if checkpoint is not None:
            checkpoint_id = checkpoint.id
            anchor_idx: int | None = None
            for idx, node in enumerate(working_nodes):
                if node.id == checkpoint.anchor_message_id:
                    anchor_idx = idx
                    break
            if anchor_idx is not None:
                working_nodes = working_nodes[anchor_idx + 1 :]

        messages: list[ChatMessage] = [node.payload for node in working_nodes]
        leaf_id = working_nodes[-1].id if working_nodes else (
            checkpoint.anchor_message_id if checkpoint else (nodes[-1].id if nodes else None)
        )

        # Inject checkpoint summary message if checkpoint is provided and has summary content
        if checkpoint is not None and checkpoint.summary.strip():
            summary_msg = ChatMessage(
                role=Role.USER,
                content=[TextBlock(text=f"[Conversation Summary]:\n{checkpoint.summary}")],
                metadata={
                    "is_checkpoint_summary": True,
                    "checkpoint_id": checkpoint.id,
                    "anchor_message_id": checkpoint.anchor_message_id,
                },
            )
            messages.insert(0, summary_msg)

        # Prepend system instruction if provided
        if system_instruction:
            has_system = bool(messages and messages[0].role == Role.SYSTEM)
            if not has_system:
                messages.insert(
                    0,
                    ChatMessage(
                        role=Role.SYSTEM,
                        content=[TextBlock(text=system_instruction)],
                    ),
                )

        if token_budget is None or not messages:
            return ContextWindow(
                messages=messages,
                leaf_node_id=leaf_id,
                checkpoint_id=checkpoint_id,
                estimated_tokens=_estimate_total_tokens(messages, self._cpt),
            )

        # Budget enforcement
        current_tokens = _estimate_total_tokens(messages, self._cpt)
        if current_tokens <= token_budget:
            return ContextWindow(
                messages=messages,
                leaf_node_id=leaf_id,
                checkpoint_id=checkpoint_id,
                estimated_tokens=current_tokens,
            )

        # Truncation: preserve system prompt and checkpoint summary if present, and retain most recent turns
        prefix_msgs: list[ChatMessage] = []
        working_msgs = list(messages)
        if working_msgs and working_msgs[0].role == Role.SYSTEM:
            prefix_msgs.append(working_msgs.pop(0))
        if working_msgs and working_msgs[0].metadata.get("is_checkpoint_summary"):
            prefix_msgs.append(working_msgs.pop(0))

        # Iteratively drop oldest delta messages until within budget
        while working_msgs and _estimate_total_tokens(
            prefix_msgs + working_msgs, self._cpt
        ) > token_budget:
            working_msgs.pop(0)
            # Ensure the first non-system/non-summary message is not an orphaned tool turn
            while working_msgs and working_msgs[0].role == Role.TOOL:
                working_msgs.pop(0)

        final_messages = prefix_msgs + working_msgs
        return ContextWindow(
            messages=final_messages,
            leaf_node_id=leaf_id,
            checkpoint_id=checkpoint_id,
            estimated_tokens=_estimate_total_tokens(final_messages, self._cpt),
        )


__all__ = ["DefaultContextBuilder"]
