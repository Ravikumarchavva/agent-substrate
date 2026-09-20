"""HistoryProvider re-export + InMemoryHistoryProvider and DefaultHistoryResolver."""

from __future__ import annotations

from typing import Any

from substrate.kernel.core.content import ChatMessage
from substrate.kernel.core.identity import Actor
from substrate.kernel.exceptions import (
    BranchAlreadyExistsError,
    BranchHeadConflictError,
    BranchNotFoundError,
    DAGIntegrityError,
)
from substrate.kernel.storage.history import (
    Branch,
    CheckpointResolver,
    HistoryCheckpoint,
    HistoryProvider,
    HistoryResolver,
    MessageNode,
)


_UNSET: Any = object()


class InMemoryHistoryProvider:
    """In-memory DAG-based HistoryProvider supporting branching and optimistic concurrency."""

    def __init__(self) -> None:
        # Legacy store: (agent_id, session_id) -> [(run_id, ChatMessage)]
        self._store: dict[tuple[Actor, str], list[tuple[str, ChatMessage]]] = {}
        # DAG store: node_id -> MessageNode
        self._nodes: dict[str, MessageNode] = {}
        # Branch store: (session_id, branch_id) -> Branch
        self._branches: dict[tuple[str, str], Branch] = {}
        # Checkpoint store: checkpoint_id -> HistoryCheckpoint
        self._checkpoints: dict[str, HistoryCheckpoint] = {}
        # Session checkpoints: session_id -> list[HistoryCheckpoint]
        self._session_checkpoints: dict[str, list[HistoryCheckpoint]] = {}

    # ── DAG Node & Branch Operations ──────────────────────────────────────────

    async def append_node(self, node: MessageNode) -> None:
        """Persist an immutable node.

        Invariants enforced:
        - Self-reference check: node.id != node.parent_id.
        - Uniqueness & Idempotency: Duplicate append is an idempotent no-op if identical,
          or raises DAGIntegrityError if any field differs.
        - Session boundary: Parent node must exist in the same session.
        """
        if node.id in self._nodes:
            existing = self._nodes[node.id]
            is_identical = (
                existing.parent_id == node.parent_id
                and existing.session_id == node.session_id
                and existing.run_id == node.run_id
                and existing.payload.model_dump(mode="json")
                == node.payload.model_dump(mode="json")
            )
            if is_identical:
                return
            raise DAGIntegrityError(
                f"Node '{node.id}' already exists with different contents"
            )

        if node.parent_id is not None:
            if node.parent_id == node.id:
                raise DAGIntegrityError(
                    f"Node '{node.id}' cannot have itself as parent"
                )
            parent = self._nodes.get(node.parent_id)
            if parent is None:
                raise DAGIntegrityError(
                    f"Parent node '{node.parent_id}' does not exist"
                )
            if parent.session_id != node.session_id:
                raise DAGIntegrityError(
                    f"Parent node belongs to session '{parent.session_id}', expected '{node.session_id}'"
                )

        self._nodes[node.id] = node

    async def get_node(self, node_id: str) -> MessageNode | None:
        return self._nodes.get(node_id)

    async def get_branch(self, session_id: str, branch_id: str) -> Branch | None:
        return self._branches.get((session_id, branch_id))

    async def list_branches(self, session_id: str) -> list[Branch]:
        return [b for (s_id, _), b in self._branches.items() if s_id == session_id]


    async def ensure_branch(
        self, session_id: str, branch_id: str, *, head_message_id: str | None = None
    ) -> Branch:
        """Fetch or initialize a branch."""
        key = (session_id, branch_id)
        if key not in self._branches:
            self._branches[key] = Branch(
                id=branch_id,
                session_id=session_id,
                head_message_id=head_message_id,
                version=0,
            )
        return self._branches[key]

    async def fork_branch(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
        *,
        fork_from_message_id: str | None = None,
    ) -> Branch:
        """Atomically fork a new branch pointer from source branch or an ancestor node."""
        new_key = (session_id, new_branch_id)
        if new_key in self._branches:
            raise BranchAlreadyExistsError(
                f"Branch '{new_branch_id}' already exists in session '{session_id}'"
            )

        source_branch = await self.get_branch(session_id, source_branch_id)
        if source_branch is None:
            raise BranchNotFoundError(
                f"Source branch '{source_branch_id}' not found in session '{session_id}'"
            )

        # Empty source branch handling
        if source_branch.head_message_id is None:
            if fork_from_message_id is not None:
                raise DAGIntegrityError(
                    f"Cannot fork from node '{fork_from_message_id}' on empty branch '{source_branch_id}'"
                )
            new_branch = Branch(
                id=new_branch_id,
                session_id=session_id,
                head_message_id=None,
                forked_from_message_id=None,
                version=0,
            )
            self._branches[new_key] = new_branch
            return new_branch

        # Non-empty source branch handling
        if fork_from_message_id is None:
            target_id = source_branch.head_message_id
        else:
            target_node = await self.get_node(fork_from_message_id)
            if target_node is None:
                raise DAGIntegrityError(
                    f"Fork node '{fork_from_message_id}' does not exist"
                )
            if target_node.session_id != session_id:
                raise DAGIntegrityError(
                    f"Fork node belongs to session '{target_node.session_id}', expected '{session_id}'"
                )

            # Verify target_node is in source branch's ancestry chain
            curr: str | None = source_branch.head_message_id
            found = False
            while curr is not None:
                if curr == fork_from_message_id:
                    found = True
                    break
                curr_node = self._nodes.get(curr)
                curr = curr_node.parent_id if curr_node else None

            if not found:
                raise DAGIntegrityError(
                    f"Node '{fork_from_message_id}' is not an ancestor of source branch head '{source_branch.head_message_id}'"
                )
            target_id = fork_from_message_id

        new_branch = Branch(
            id=new_branch_id,
            session_id=session_id,
            head_message_id=target_id,
            forked_from_message_id=target_id,
            version=0,
        )
        self._branches[new_key] = new_branch
        return new_branch

    async def rename_branch(
        self, session_id: str, branch_id: str, new_name: str
    ) -> Branch:
        """Rename an existing branch display name."""
        key = (session_id, branch_id)
        old_branch = self._branches.get(key)
        if old_branch is None:
            raise BranchNotFoundError(
                f"Branch '{branch_id}' not found in session '{session_id}'"
            )

        updated_branch = Branch(
            id=old_branch.id,
            session_id=session_id,
            name=new_name,
            head_message_id=old_branch.head_message_id,
            forked_from_message_id=old_branch.forked_from_message_id,
            version=old_branch.version + 1,
            created_at=old_branch.created_at,
        )
        self._branches[key] = updated_branch
        return updated_branch

    async def set_branch_head(
        self,
        session_id: str,
        branch_id: str,
        new_head_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        """Advance branch head pointer using optimistic concurrency control."""
        branch = await self.get_branch(session_id, branch_id)
        if branch is None:
            raise BranchNotFoundError(
                f"Branch '{branch_id}' not found in session '{session_id}'"
            )

        # CAS checks
        if expected_head_id is not _UNSET:
            if branch.head_message_id != expected_head_id:
                raise BranchHeadConflictError(
                    f"Branch head conflict: expected '{expected_head_id}', actual '{branch.head_message_id}'",
                    session_id=session_id,
                    branch_id=branch_id,
                    expected=expected_head_id,
                    actual=branch.head_message_id,
                )

        if expected_version is not None:
            if branch.version != expected_version:
                raise BranchHeadConflictError(
                    f"Branch version conflict: expected {expected_version}, actual {branch.version}",
                    session_id=session_id,
                    branch_id=branch_id,
                    expected=expected_version,
                    actual=branch.version,
                )

        new_head = await self.get_node(new_head_id)
        if new_head is None:
            raise DAGIntegrityError(f"New head node '{new_head_id}' does not exist")
        if new_head.session_id != session_id:
            raise DAGIntegrityError(
                f"New head node belongs to session '{new_head.session_id}', expected '{session_id}'"
            )

        updated_branch = Branch(
            id=branch_id,
            session_id=session_id,
            head_message_id=new_head_id,
            forked_from_message_id=branch.forked_from_message_id,
            version=branch.version + 1,
        )
        self._branches[(session_id, branch_id)] = updated_branch
        return updated_branch

    async def append_and_advance(
        self,
        node: MessageNode,
        branch_id: str,
        *,
        expected_head_id: str | None = _UNSET,
        expected_version: int | None = None,
    ) -> Branch:
        """Atomically append node and advance branch head.

        Enforces:
        - node.parent_id == current branch head.
        - Concurrency checks on expected_head_id and expected_version.
        """
        key = (node.session_id, branch_id)
        branch = self._branches.get(key)
        if branch is None:
            # Auto-initialize branch if not existing
            branch = Branch(
                id=branch_id,
                session_id=node.session_id,
                head_message_id=None,
                version=0,
            )
            self._branches[key] = branch

        current_head = branch.head_message_id

        # Invariant: node.parent_id must match current branch head!
        if node.parent_id != current_head:
            raise BranchHeadConflictError(
                f"Cannot advance branch '{branch_id}': node parent '{node.parent_id}' does not match current head '{current_head}'",
                session_id=node.session_id,
                branch_id=branch_id,
                expected=current_head,
                actual=node.parent_id,
            )

        # CAS verification
        if expected_head_id is not _UNSET:
            if current_head != expected_head_id:
                raise BranchHeadConflictError(
                    f"Branch head conflict on advance: expected '{expected_head_id}', actual '{current_head}'",
                    session_id=node.session_id,
                    branch_id=branch_id,
                    expected=expected_head_id,
                    actual=current_head,
                )

        if expected_version is not None:
            if branch.version != expected_version:
                raise BranchHeadConflictError(
                    f"Branch version conflict on advance: expected {expected_version}, actual {branch.version}",
                    session_id=node.session_id,
                    branch_id=branch_id,
                    expected=expected_version,
                    actual=branch.version,
                )

        # Atomic commit: append node + advance head
        await self.append_node(node)

        updated_branch = Branch(
            id=branch_id,
            session_id=node.session_id,
            head_message_id=node.id,
            forked_from_message_id=branch.forked_from_message_id,
            version=branch.version + 1,
        )
        self._branches[key] = updated_branch
        return updated_branch

    # ── Checkpoint Operations ────────────────────────────────────────────────

    async def save_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        """Persist a compaction checkpoint anchor."""
        anchor = await self.get_node(checkpoint.anchor_message_id)
        if anchor is None:
            raise DAGIntegrityError(
                f"Checkpoint anchor node '{checkpoint.anchor_message_id}' does not exist"
            )
        if anchor.session_id != checkpoint.session_id:
            raise DAGIntegrityError(
                f"Checkpoint anchor belongs to session '{anchor.session_id}', expected '{checkpoint.session_id}'"
            )
        self._checkpoints[checkpoint.id] = checkpoint
        self._session_checkpoints.setdefault(checkpoint.session_id, []).append(checkpoint)

    async def get_checkpoint(self, checkpoint_id: str) -> HistoryCheckpoint | None:
        return self._checkpoints.get(checkpoint_id)

    async def list_checkpoints(self, session_id: str) -> list[HistoryCheckpoint]:
        return list(self._session_checkpoints.get(session_id, []))

    # ── Legacy / Linear Compatibility Methods ───────────────────────────────

    @staticmethod
    def _tag(message: ChatMessage, run_id: str) -> ChatMessage:
        if not run_id or message.metadata.get("run_id") == run_id:
            return message
        return message.model_copy(
            update={"metadata": {**message.metadata, "run_id": run_id}}
        )

    async def append(
        self,
        agent_id: Actor,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        tagged = self._tag(message, run_id)
        self._store.setdefault((agent_id, session_id), []).append((run_id, tagged))

    async def append_many(
        self,
        agent_id: Actor,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        bucket = self._store.setdefault((agent_id, session_id), [])
        bucket.extend((run_id, self._tag(m, run_id)) for m in messages)

    async def get_messages(
        self,
        agent_id: Actor,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        pairs = self._store.get((agent_id, session_id), [])
        msgs = [m for _, m in pairs]
        if offset is not None:
            msgs = msgs[offset:]
        if limit is not None:
            msgs = msgs[:limit]
        return msgs

    async def clear(self, agent_id: Actor, *, session_id: str) -> None:
        self._store.pop((agent_id, session_id), None)

    async def clear_run(
        self, agent_id: Actor, *, session_id: str, run_id: str
    ) -> None:
        key = (agent_id, session_id)
        if key in self._store:
            self._store[key] = [
                (rid, m) for rid, m in self._store[key] if rid != run_id
            ]

    async def count_messages(self, agent_id: Actor, *, session_id: str) -> int:
        return len(self._store.get((agent_id, session_id), []))


class DefaultHistoryResolver:
    """Encapsulates parent-pointer walking over a HistoryProvider."""

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
        history_resolver: HistoryResolver | None = None,
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


__all__ = [
    "HistoryProvider",
    "HistoryResolver",
    "CheckpointResolver",
    "InMemoryHistoryProvider",
    "DefaultHistoryResolver",
    "AncestryCheckpointResolver",
]
