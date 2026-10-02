"""The DAG resolvers and the linear ``project_messages`` view over any ``HistoryProvider``."""

from __future__ import annotations

from typing import Any

from substrate.types.content import ChatMessage
from substrate.types.errors import DAGIntegrityError
from substrate.stores.threads import HistoryCheckpoint, HistoryProvider, MessageNode

_UNSET: Any = object()


class DefaultHistoryResolver:
    """Walks parent pointers over a HistoryProvider."""

    def __init__(self, provider: HistoryProvider) -> None:
        self._provider = provider

    async def resolve_ancestry(
        self,
        leaf_message_id: str,
        *,
        stop_at_node_id: str | None = None,
    ) -> list[MessageNode]:
        """Walk parent_id edges from leaf back to root (or stop_at_node_id), returning chronological order."""
        chain: list[MessageNode] = []
        visited: set[str] = set()
        curr: str | None = leaf_message_id

        while curr is not None:
            if curr in visited:
                raise DAGIntegrityError(
                    f"Cycle detected in ancestry path at node '{curr}'"
                )
            visited.add(curr)

            if curr == stop_at_node_id:
                break

            node = await self._provider.get_node(curr)
            if node is None:
                raise DAGIntegrityError(f"Broken DAG: ancestor node '{curr}' not found")
            chain.append(node)
            curr = node.parent_id

        chain.reverse()  # Oldest (root/boundary) -> newest (leaf)
        return chain


class AncestryCheckpointResolver:
    """Locates checkpoints valid for a branch lineage by traversing ancestry.

    Invariants enforced:
    - A checkpoint is applicable if and only if its anchor_message_id is an ancestor
      of (or equal to) the requested leaf_message_id.
    - Checkpoints on shared ancestors are valid for all descendant branches.
    - Checkpoints on divergent branches (not in leaf's ancestry chain) are rejected.
    - When multiple checkpoints apply, the one with the greatest topological depth
      (nearest anchor to leaf_message_id) is selected.
    - If multiple checkpoints exist for the exact same nearest anchor, the most recently
      created checkpoint is selected.
    """

    def __init__(
        self,
        provider: HistoryProvider,
        history_resolver: DefaultHistoryResolver | None = None,
    ) -> None:
        self._provider = provider
        self._resolver = history_resolver or DefaultHistoryResolver(provider)

    async def find_applicable_checkpoint(
        self,
        leaf_message_id: str,
    ) -> HistoryCheckpoint | None:
        leaf = await self._provider.get_node(leaf_message_id)
        if leaf is None:
            raise DAGIntegrityError(f"Leaf node '{leaf_message_id}' not found")

        checkpoints = await self._provider.list_checkpoints(leaf.session_id)
        if not checkpoints:
            return None

        # Group checkpoints by anchor_message_id
        by_anchor: dict[str, list[HistoryCheckpoint]] = {}
        for cp in checkpoints:
            by_anchor.setdefault(cp.anchor_message_id, []).append(cp)

        # Resolve ancestry from root to leaf
        ancestry = await self._resolver.resolve_ancestry(leaf_message_id)

        # Walk backwards from leaf to root to find nearest anchor (greatest topological depth)
        for node in reversed(ancestry):
            if node.id in by_anchor:
                candidates = by_anchor[node.id]
                if len(candidates) == 1:
                    return candidates[0]
                # Multiple checkpoints at the exact same anchor: select the most recently created
                return max(candidates, key=lambda c: c.created_at)

        return None


async def project_messages(
    history: HistoryProvider,
    session_id: str,
    *,
    branch_id: str = "main",
    builder: Any = None,
) -> list[ChatMessage]:
    """Linear, LLM-ready view of one branch: ancestry, latest applicable
    checkpoint, then the context builder. ``[]`` for an unknown or empty branch.

    The single read path for conversation history — the linear transcript is
    derived from the DAG, never stored separately.
    """
    branch = await history.get_branch(session_id, branch_id)
    if branch is None or branch.head_message_id is None:
        return []
    nodes = await DefaultHistoryResolver(history).resolve_ancestry(
        branch.head_message_id
    )
    checkpoint = await AncestryCheckpointResolver(history).find_applicable_checkpoint(
        branch.head_message_id
    )
    if builder is None:
        from substrate.context.builder import DefaultContextBuilder

        builder = DefaultContextBuilder()
    window = await builder.build(nodes, checkpoint=checkpoint)
    return list(window.messages)


__all__ = [
    "HistoryProvider",
    "DefaultHistoryResolver",
    "AncestryCheckpointResolver",
    "project_messages",
]
