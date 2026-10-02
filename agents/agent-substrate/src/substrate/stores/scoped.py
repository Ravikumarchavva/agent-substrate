"""Scope-bound store handles.

``bind_*(store, scope)`` returns a handle that satisfies the same port as ``store`` but can only see
``scope``'s tenant. It works by placing everything a caller names — a session, a collection, a task
conversation, an object key, a graph namespace — under the tenant on the way in and removing the tenant on
the way out, and by refusing any id that resolves to another tenant's record. It is written once over the
ports, so it holds for every implementation (local, Postgres, S3, Lance…) without each re-implementing it.

The tenant is percent-encoded (injective, and it contains no ``/``), so ``"a/b"`` and ``"a"`` can never share
a prefix, and a caller-chosen name cannot be crafted to look like a different tenant's.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from substrate.types.scope import Scope
from substrate.stores.graph import Entity, GraphStore, Relationship, SubGraph
from substrate.stores.threads import Branch, HistoryCheckpoint, ThreadStore, MessageNode
from substrate.stores.files import FileStore
from substrate.stores.tasks import Task, TaskList, TaskStatus, TaskStore
from substrate.stores.vector import Document, SearchResult, VectorStore


def _tenant(scope: Scope) -> str:
    return quote(scope.tenant_id, safe="")


class _Prefixer:
    """Names under ``<tenant>/`` and back."""

    def __init__(self, scope: Scope) -> None:
        self.prefix = _tenant(scope) + "/"

    def add(self, name: str) -> str:
        return self.prefix + name

    def strip(self, name: str) -> str:
        return name[len(self.prefix) :]

    def owns(self, name: str) -> bool:
        return name.startswith(self.prefix)


# ===================================================================== history


class _ScopedThreadStore:
    """A ``ThreadStore`` that sees only its tenant's sessions."""

    def __init__(self, inner: ThreadStore, scope: Scope) -> None:
        self._inner = inner
        self._p = _Prefixer(scope)

    def _node_in(self, node: MessageNode) -> MessageNode:
        return node.model_copy(update={"session_id": self._p.add(node.session_id)})

    def _node_out(self, node: MessageNode) -> MessageNode:
        return node.model_copy(update={"session_id": self._p.strip(node.session_id)})

    def _branch_out(self, branch: Branch) -> Branch:
        return branch.model_copy(
            update={"session_id": self._p.strip(branch.session_id)}
        )

    def _cp_out(self, cp: HistoryCheckpoint) -> HistoryCheckpoint:
        return cp.model_copy(update={"session_id": self._p.strip(cp.session_id)})

    async def _mine(self, node_id: str | None) -> MessageNode | None:
        if node_id is None:
            return None
        node = await self._inner.get_node(node_id)
        return node if node is not None and self._p.owns(node.session_id) else None

    async def append_node(self, node: MessageNode) -> None:
        await self._inner.append_node(self._node_in(node))

    async def get_node(self, node_id: str) -> MessageNode | None:
        node = await self._mine(node_id)
        return self._node_out(node) if node else None

    async def get_branch(self, session_id: str, branch_id: str) -> Branch | None:
        b = await self._inner.get_branch(self._p.add(session_id), branch_id)
        return self._branch_out(b) if b else None

    async def list_branches(self, session_id: str) -> list[Branch]:
        return [
            self._branch_out(b)
            for b in await self._inner.list_branches(self._p.add(session_id))
        ]

    async def ensure_branch(
        self, session_id: str, branch_id: str, *, head_message_id: str | None = None
    ) -> Branch:
        if head_message_id is not None and await self._mine(head_message_id) is None:
            raise ValueError(f"head {head_message_id!r} is not a node of this scope")
        return self._branch_out(
            await self._inner.ensure_branch(
                self._p.add(session_id), branch_id, head_message_id=head_message_id
            )
        )

    async def rename_branch(
        self, session_id: str, branch_id: str, new_name: str
    ) -> Branch:
        return self._branch_out(
            await self._inner.rename_branch(
                self._p.add(session_id), branch_id, new_name
            )
        )

    async def fork_branch(
        self,
        session_id: str,
        source_branch_id: str,
        new_branch_id: str,
        *,
        fork_from_message_id: str | None = None,
    ) -> Branch:
        return self._branch_out(
            await self._inner.fork_branch(
                self._p.add(session_id),
                source_branch_id,
                new_branch_id,
                fork_from_message_id=fork_from_message_id,
            )
        )

    async def set_branch_head(
        self,
        session_id: str,
        branch_id: str,
        new_head_id: str,
        *,
        expected_head_id: str | None = None,
        expected_version: int | None = None,
    ) -> Branch:
        if await self._mine(new_head_id) is None:
            raise ValueError(f"head {new_head_id!r} is not a node of this scope")
        kwargs: dict[str, Any] = {"expected_version": expected_version}
        if expected_head_id is not None:
            kwargs["expected_head_id"] = expected_head_id
        return self._branch_out(
            await self._inner.set_branch_head(
                self._p.add(session_id), branch_id, new_head_id, **kwargs
            )
        )

    async def append_and_advance(
        self,
        node: MessageNode,
        branch_id: str,
        *,
        expected_head_id: str | None = None,
        expected_version: int | None = None,
    ) -> Branch:
        kwargs: dict[str, Any] = {"expected_version": expected_version}
        if expected_head_id is not None:
            kwargs["expected_head_id"] = expected_head_id
        return self._branch_out(
            await self._inner.append_and_advance(
                self._node_in(node), branch_id, **kwargs
            )
        )

    async def save_checkpoint(self, checkpoint: HistoryCheckpoint) -> None:
        if await self._mine(checkpoint.anchor_message_id) is None:
            raise ValueError("a checkpoint must anchor on a node of its own scope")
        await self._inner.save_checkpoint(
            checkpoint.model_copy(
                update={"session_id": self._p.add(checkpoint.session_id)}
            )
        )

    async def get_checkpoint(self, checkpoint_id: str) -> HistoryCheckpoint | None:
        cp = await self._inner.get_checkpoint(checkpoint_id)
        return (
            self._cp_out(cp) if cp is not None and self._p.owns(cp.session_id) else None
        )

    async def list_checkpoints(self, session_id: str) -> list[HistoryCheckpoint]:
        return [
            self._cp_out(c)
            for c in await self._inner.list_checkpoints(self._p.add(session_id))
        ]

    async def delete_branch(self, session_id: str, branch_id: str) -> None:
        await self._inner.delete_branch(self._p.add(session_id), branch_id)

    async def delete_session(self, session_id: str) -> None:
        await self._inner.delete_session(self._p.add(session_id))


# ===================================================================== vector


class _ScopedVectorStore:
    """A ``VectorStore`` whose collections all live inside its tenant — and, if the store beneath searches by words
    (``SearchableVectorStore``), a searchable one: ``lexical_search`` and ``hybrid_search`` are confined the same way."""

    def __init__(self, inner: VectorStore, scope: Scope) -> None:
        self._inner = inner
        self._p = _Prefixer(scope)

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
        space: str | None = None,
    ) -> list[str]:
        return await self._inner.add(
            documents, collection=self._p.add(collection), space=space
        )

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
        space: str | None = None,
    ) -> list[SearchResult]:
        return await self._inner.search(
            query_embedding,
            collection=self._p.add(collection),
            limit=limit,
            filter=filter,
            space=space,
        )

    async def lexical_search(
        self,
        query_text: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
        match: str = "all",
    ) -> list[SearchResult]:
        return await self._inner.lexical_search(
            query_text,
            collection=self._p.add(collection),
            limit=limit,
            filter=filter,
            match=match,
        )  # type: ignore[attr-defined]

    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        *,
        collection: str = "default",
        dense_k: int = 50,
        lexical_k: int = 50,
        fused_k: int = 50,
        rrf_k: int = 60,
        filter: dict[str, Any] | None = None,
        space: str | None = None,
    ) -> list[SearchResult]:
        return await self._inner.hybrid_search(  # type: ignore[attr-defined]
            query_embedding,
            query_text,
            collection=self._p.add(collection),
            dense_k=dense_k,
            lexical_k=lexical_k,
            fused_k=fused_k,
            rrf_k=rrf_k,
            filter=filter,
            space=space,
        )

    async def get(
        self, ids: list[str], *, collection: str = "default"
    ) -> list[Document]:
        return await self._inner.get(ids, collection=self._p.add(collection))

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
        space: str | None = None,
    ) -> list[str]:
        return await self._inner.upsert(
            documents, collection=self._p.add(collection), space=space
        )

    async def delete(self, ids: list[str], *, collection: str = "default") -> int:
        return await self._inner.delete(ids, collection=self._p.add(collection))

    async def list_collections(self) -> list[str]:
        return [
            self._p.strip(c)
            for c in await self._inner.list_collections()
            if self._p.owns(c)
        ]

    async def delete_collection(self, collection: str) -> int:
        return await self._inner.delete_collection(self._p.add(collection))

    async def rename_collection(self, old: str, new: str) -> int:
        return await self._inner.rename_collection(self._p.add(old), self._p.add(new))

    async def erase(self) -> int:
        """Remove every collection of this tenant. Returns how many."""
        mine = await self.list_collections()
        for name in mine:
            await self.delete_collection(name)
        return len(mine)


# ===================================================================== graph


class _ScopedGraphStore:
    """A ``GraphStore`` whose every call runs in its tenant's namespace. A caller's own ``namespace``
    subdivides the tenant; it can never leave it."""

    def __init__(self, inner: GraphStore, scope: Scope) -> None:
        self._inner = inner
        self._tenant = _tenant(scope)

    def _ns(self, namespace: str) -> str:
        return f"{self._tenant}/{namespace}" if namespace else self._tenant

    async def add_entities(
        self, entities: list[Entity], *, namespace: str = ""
    ) -> list[str]:
        return await self._inner.add_entities(entities, namespace=self._ns(namespace))

    async def add_relationships(
        self, relationships: list[Relationship], *, namespace: str = ""
    ) -> list[str]:
        return await self._inner.add_relationships(
            relationships, namespace=self._ns(namespace)
        )

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
        namespace: str = "",
    ) -> SubGraph:
        return await self._inner.get_neighbors(
            entity_id,
            depth=depth,
            relationship_types=relationship_types,
            namespace=self._ns(namespace),
        )

    async def delete_entity(self, entity_id: str, *, namespace: str = "") -> bool:
        return await self._inner.delete_entity(entity_id, namespace=self._ns(namespace))

    async def delete_relationship(
        self, relationship_id: str, *, namespace: str = ""
    ) -> bool:
        return await self._inner.delete_relationship(
            relationship_id, namespace=self._ns(namespace)
        )


# ===================================================================== objects


class _FencedFileStore:
    """An ``FileStore`` that keeps its callers' *absolute* keys but confines them to ``tenants/<tenant>/``.

    Serving builds absolute keys through ``workspace/layout`` and stores them in database rows, so what it needs is a
    fence: every key and prefix must be inside the tenant's subtree, with nothing that could climb out of it.
    Anything else raises ``ValueError`` before the store is touched.
    """

    def __init__(self, inner: FileStore, scope: Scope) -> None:
        if "/" in scope.tenant_id or "\\" in scope.tenant_id:
            raise ValueError("a tenant id with a path separator cannot be fenced")
        self._inner = inner
        self._scope = scope
        self._root = f"tenants/{scope.tenant_id}/"

    def _in(self, key: str, *, prefix: bool = False) -> str:
        parts = key.split("/")
        if (
            not key.startswith(self._root)
            or "\\" in key
            or "\x00" in key
            or any(p in ("..", ".") for p in parts)
            or (not prefix and key.endswith("/"))
        ):
            raise ValueError(f"key {key!r} is outside tenant {self._scope.tenant_id!r}")
        return key

    async def upload(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        await self._inner.upload(self._in(key), data, content_type=content_type)

    async def download(self, key: str) -> bytes:
        return await self._inner.download(self._in(key))

    async def exists(self, key: str) -> bool:
        return await self._inner.exists(self._in(key))

    async def delete(self, key: str) -> None:
        await self._inner.delete(self._in(key))

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        return await self._inner.list_prefix(self._in(prefix, prefix=True))

    async def delete_prefix(self, prefix: str) -> int:
        return await self._inner.delete_prefix(self._in(prefix, prefix=True))

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        return await self._inner.copy_prefix(
            self._in(source_prefix, prefix=True), self._in(dest_prefix, prefix=True)
        )

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        return await self._inner.presign_url(self._in(key), expires_in=expires_in)

    async def usage_bytes(
        self, tenant_id: str | None = None, *, force: bool = False
    ) -> int:
        if tenant_id is not None and tenant_id != self._scope.tenant_id:
            raise ValueError("a fenced store reports only its own tenant's usage")
        return await self._inner.usage_bytes(self._scope.tenant_id, force=force)

    async def erase(self) -> int:
        """Remove everything this tenant has stored. Returns how many objects."""
        return await self._inner.delete_prefix(self._root)


# ===================================================================== tasks


class _ScopedTaskStore:
    """A ``TaskStore`` whose conversations all live inside its tenant."""

    def __init__(self, inner: TaskStore, scope: Scope) -> None:
        self._inner = inner
        self._p = _Prefixer(scope)

    def _out(self, board: TaskList | None) -> TaskList | None:
        return (
            board.model_copy(
                update={"conversation_id": self._p.strip(board.conversation_id)}
            )
            if board is not None
            else None
        )

    async def _owned(self, task_list_id: str) -> bool:
        board = await self._inner.get_task_list(task_list_id)
        return board is not None and self._p.owns(board.conversation_id)

    async def create_task_list(
        self,
        conversation_id: str,
        task_titles: list[str],
        *,
        agent_id: str = "",
        agent_label: str = "",
        parent_agent_id: str | None = None,
        max_retries: int = 3,
        branch_id: str = "main",
    ) -> TaskList:
        board = await self._inner.create_task_list(
            self._p.add(conversation_id),
            task_titles,
            agent_id=agent_id,
            agent_label=agent_label,
            parent_agent_id=parent_agent_id,
            max_retries=max_retries,
            branch_id=branch_id,
        )
        return self._out(board)  # type: ignore[return-value]

    async def get_task_list(self, task_list_id: str) -> TaskList | None:
        board = await self._inner.get_task_list(task_list_id)
        return (
            self._out(board)
            if board is not None and self._p.owns(board.conversation_id)
            else None
        )

    async def get_by_conversation(
        self, conversation_id: str, branch_id: str = "main"
    ) -> TaskList | None:
        return self._out(
            await self._inner.get_by_conversation(
                self._p.add(conversation_id), branch_id
            )
        )

    async def get_boards_by_conversation(
        self, conversation_id: str, branch_id: str = "main"
    ) -> list[TaskList]:
        return [
            self._out(b)
            for b in await self._inner.get_boards_by_conversation(
                self._p.add(conversation_id), branch_id
            )
        ]  # type: ignore[misc]

    async def settle_conversation(self, conversation_id: str) -> list[TaskList]:
        return [
            self._out(b)
            for b in await self._inner.settle_conversation(self._p.add(conversation_id))
        ]  # type: ignore[attr-defined,misc]

    async def update_status(
        self, task_list_id: str, task_id: str, status: TaskStatus, note: str = ""
    ) -> Task | None:
        return (
            await self._inner.update_status(task_list_id, task_id, status, note)
            if await self._owned(task_list_id)
            else None
        )

    async def add_tasks(self, task_list_id: str, titles: list[str]) -> list[Task]:
        return (
            await self._inner.add_tasks(task_list_id, titles)
            if await self._owned(task_list_id)
            else []
        )

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        return (
            await self._inner.delete_task(task_list_id, task_id)
            if await self._owned(task_list_id)
            else False
        )

    async def increment_retry(self, task_list_id: str, task_id: str) -> Task | None:
        return (
            await self._inner.increment_retry(task_list_id, task_id)
            if await self._owned(task_list_id)
            else None
        )

    async def force_retry(self, task_list_id: str, task_id: str) -> Task | None:
        return (
            await self._inner.force_retry(task_list_id, task_id)
            if await self._owned(task_list_id)
            else None
        )

    async def update_task_title(
        self, task_list_id: str, task_id: str, title: str
    ) -> Task | None:
        return (
            await self._inner.update_task_title(task_list_id, task_id, title)
            if await self._owned(task_list_id)
            else None
        )


def bind_threads(store: ThreadStore, scope: Scope) -> ThreadStore:
    return _ScopedThreadStore(store, scope)


def bind_vector(store: VectorStore, scope: Scope) -> VectorStore:
    return _ScopedVectorStore(store, scope)


def bind_graph(store: GraphStore, scope: Scope) -> GraphStore:
    return _ScopedGraphStore(store, scope)


def fence_objects(store: FileStore, scope: Scope) -> FileStore:
    return _FencedFileStore(store, scope)


def bind_tasks(store: TaskStore, scope: Scope) -> TaskStore:
    return _ScopedTaskStore(store, scope)


__all__ = ["bind_graph", "bind_tasks", "bind_threads", "bind_vector", "fence_objects"]
