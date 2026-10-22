from __future__ import annotations

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.context import DefaultHistoryResolver
from loamz.engine.agents.kernel.ports.files import FileStore
from loamz.engine.agents.kernel.ports.threads import Branch, HistoryCheckpoint, ThreadStore
from loamz.engine.agents.kernel.types.scope import Scope
from loamz.engine.agents.stores import AgentStore, bind_threads, fence_objects
from loamz.engine.agents.stores.workspace import (
    delete_branch_workspace,
    fork_branch,
    resolve_workspace_snapshot_id,
)
from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.branches.model import (
    BranchForkRequest,
    BranchList,
    BranchMessages,
    BranchOut,
    CheckpointCreateRequest,
    CheckpointList,
    CheckpointOut,
)
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads


def _out(b: Branch) -> BranchOut:
    return BranchOut(
        id=b.id,
        session_id=b.session_id,
        name=b.name,
        head_message_id=b.head_message_id,
        forked_from_message_id=b.forked_from_message_id,
        version=b.version,
        created_at=b.created_at,
    )


class BranchesService:
    """A conversation's branches (movable pointers into its message DAG), their workspaces and checkpoints."""

    def __init__(self, store: AgentStore, files: FileStore, threads_: ThreadsService) -> None:
        self._store = store
        self._files = files
        self._threads = threads_

    def _history(self, caller: Caller) -> ThreadStore:
        """The conversation store as the caller's tenant sees it: another tenant's ids resolve to nothing."""
        return bind_threads(self._store.threads, Scope(tenant_id=caller.tenant_id))

    async def _branch(self, caller: Caller, thread_id: str, branch_id: str) -> Branch:
        history = self._history(caller)
        branch = await history.get_branch(thread_id, branch_id)
        if branch is None and branch_id == "main":
            branch = await history.ensure_branch(thread_id, "main")
        if branch is None:
            raise NotFoundError(f"Branch '{branch_id}' not found")
        return branch

    async def list(self, caller: Caller, thread_id: str) -> BranchList:
        await self._threads.require(caller, thread_id)
        history = self._history(caller)
        branches = await history.list_branches(thread_id)
        if not any(b.id == "main" for b in branches):
            branches = [await history.ensure_branch(thread_id, "main"), *branches]
        return BranchList(items=[_out(b) for b in branches])

    async def fork(self, caller: Caller, thread_id: str, body: BranchForkRequest) -> BranchOut:
        """A new branch pointer, with its workspace pointed at the right snapshot (O(1), no bytes copied)."""
        await self._threads.require(caller, thread_id)
        history = self._history(caller)
        await history.ensure_branch(thread_id, body.source_branch_id)
        source = await history.get_branch(thread_id, body.source_branch_id)
        source_head = source.head_message_id if source else None
        fork_at = body.fork_from_message_id
        if fork_at:
            node = await history.get_node(fork_at)
            if node is None or node.session_id != thread_id:
                fork_at = source_head
        created = await history.fork_branch(
            thread_id,
            source_branch_id=body.source_branch_id,
            new_branch_id=body.new_branch_id,
            fork_from_message_id=fork_at,
        )
        # A fork "from the past" starts from the files as they were then, not from now.
        try:
            from_snapshot = None
            if fork_at and fork_at != source_head:
                from_snapshot = await resolve_workspace_snapshot_id(history, fork_at)
            await fork_branch(
                self._store.workspaces,
                session_id=thread_id,
                source_branch_id=body.source_branch_id,
                new_branch_id=body.new_branch_id,
                from_snapshot_id=from_snapshot,
            )
        except ValueError:
            pass  # the new branch already has a workspace pointer
        return _out(created)

    async def get(self, caller: Caller, thread_id: str, branch_id: str) -> BranchOut:
        await self._threads.require(caller, thread_id)
        return _out(await self._branch(caller, thread_id, branch_id))

    async def rename(self, caller: Caller, thread_id: str, branch_id: str, name: str) -> BranchOut:
        await self._threads.require(caller, thread_id)
        name = name.strip()
        if not name:
            raise InvalidRequestError("Branch name cannot be empty")
        return _out(await self._history(caller).rename_branch(thread_id, branch_id, name))

    async def delete(self, caller: Caller, thread_id: str, branch_id: str) -> None:
        """Delete the pointer and its workspace prefix at once (the DAG nodes stay, as `ThreadStore` documents)."""
        header = await self._threads.require(caller, thread_id)
        try:
            await self._history(caller).delete_branch(thread_id, branch_id)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from None
        await delete_branch_workspace(
            fence_objects(self._files, Scope(tenant_id=caller.tenant_id)),
            tenant_id=caller.tenant_id,
            user_id=header.user_id,
            conversation_id=thread_id,
            branch_id=branch_id,
        )

    async def messages(self, caller: Caller, thread_id: str, branch_id: str) -> BranchMessages:
        """Messages in order along this branch's ancestry."""
        await self._threads.require(caller, thread_id)
        branch = await self._branch(caller, thread_id, branch_id)
        if branch.head_message_id is None:
            return BranchMessages(items=[])
        nodes = await DefaultHistoryResolver(self._history(caller)).resolve_ancestry(
            branch.head_message_id
        )
        return BranchMessages(
            items=[
                {
                    "id": n.id,
                    "parent_id": n.parent_id,
                    "role": n.payload.role,
                    "content": [b.model_dump() for b in n.payload.content],
                    "text": n.payload.text,
                    "created_at": n.created_at.isoformat(),
                }
                for n in nodes
            ]
        )

    async def checkpoints(self, caller: Caller, thread_id: str) -> CheckpointList:
        await self._threads.require(caller, thread_id)
        return CheckpointList(
            items=[
                CheckpointOut(**cp.model_dump())
                for cp in await self._history(caller).list_checkpoints(thread_id)
            ]
        )

    async def checkpoint(
        self, caller: Caller, thread_id: str, body: CheckpointCreateRequest
    ) -> CheckpointOut:
        """Persist a compaction checkpoint anchor by hand."""
        await self._threads.require(caller, thread_id)
        cp = HistoryCheckpoint(
            session_id=thread_id,
            anchor_message_id=body.anchor_message_id,
            summary=body.summary,
            state=body.state or {},
        )
        await self._history(caller).save_checkpoint(cp)
        return CheckpointOut(**cp.model_dump())


def branches(container: Container) -> BranchesService:
    return BranchesService(container.agent_store, container.file_store, container.get(threads))
