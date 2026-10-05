"""StagedSandboxRuntime — materialize a branch's workspace before a run, commit it after.

Every runtime — nsjail bind-mounting a directory, an in-pod k8s server writing
to its own local disk, the inprocess test runtime — executes against a real
local filesystem tree. But the durable source of truth is no longer a
filesystem at all: it's a content-addressed manifest (``agents/workspace/``).
This wrapper is the one place that bridges the two: before a run it
materializes the branch's current snapshot into local scratch (copying
from the CAS's local cache where possible — see
``workspace/materialize.py``), and after a run it hashes whatever
changed and commits a new snapshot, advancing the branch head.

This wraps every *local-process* runtime now (nsjail, inprocess) — there is
no more filesystem shortcut for the "local" ``FILE_STORE_BACKEND``: the
object store holds content-addressed blobs
(``tenants/{t}/users/{u}/blobs/{hash}``), not a browsable tree, so even
nsjail (same host as the object store) needs a materialized scratch copy to
bind-mount.

Deliberately NOT used for ``K8sRuntime``: that runtime never reads
``spec.session_dir`` at all — pod/PVC selection happens inside
``CodeInterpreterService`` by ``(tenant_id, user_id)`` alone, on a different
node than wherever this wrapper would materialize local scratch. Wrapping
it here would be worse than doing nothing: stage-out would commit an
empty/stale snapshot every run, since the pod never touches the scratch
dir this class watches. k8s stays per-user-isolated (unchanged from
before), not yet per-branch — see ``substrate_cloud/factory.py``'s
runtime wiring for where that's enforced.

A conversation's workspace is a snapshot, as above. An agent's home and a group's drive (``is_persistent_workspace``) are not: they are the
object tree itself, the one that uploads and the Files list use, so they are kept in step with it by ``PrefixSync`` instead — what is uploaded
is there for the code, and what the code makes is in the Files list. A run can have several of those open at once (``spec.mounts``).

Scratch is disposable by construction: anything a run needs is checked out
from the workspace store, and anything worth keeping is committed back. That
makes it safe for the local tree to be a container's ephemeral disk.
"""

from __future__ import annotations

import logging

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from substrate.workspace import BlobCAS
from substrate.workspace.materialize import materialize as materialize_manifest
from substrate.workspace import WorkspaceScope
from substrate.workspace import commit_turn
from substrate.types import SnapshotConflictError
from substrate.stores import FileStore
from substrate.workspace import WorkspaceStore
from substrate.workspace.layout import conversation_shared_prefix, is_persistent_workspace

from .base import ExecResult, SandboxSpec
from .prefix_sync import PrefixSync

logger = logging.getLogger(__name__)


class StagedSandboxRuntime:
    """Materialize a branch's workspace in, run *inner*, commit it back out.

    Delegates isolation entirely to *inner* — this class only moves bytes
    between the durable CAS/snapshot store and a local scratch tree, and
    deliberately does not touch how ``spec`` is otherwise enforced, so the
    wrapped runtime still enforces the same boundary it always did.
    """

    def __init__(
        self,
        inner: Any,
        *,
        object_store: FileStore,
        workspace_store: WorkspaceStore,
        scratch_root: str | Path,
    ) -> None:
        self._inner = inner
        self._object_store = object_store
        self._workspace_store = workspace_store
        self._root = Path(scratch_root).resolve()
        self._cache_root = self._root / ".cas-cache"
        self._trees = PrefixSync(object_store, self._root / ".sync")
        # One lock per session_dir: two concurrent runs on the same branch
        # in this process would otherwise interleave checkout and commit and
        # could commit a half-written tree. Cross-process races are still
        # possible (multiple workers) — that's what expected_parent_id on
        # commit_turn is for; see _stage_out.
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def name(self) -> str:
        return f"staged({self._inner.name})"

    def _lock_for(self, session_dir: str) -> asyncio.Lock:
        lock = self._locks.get(session_dir)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_dir] = lock
        return lock

    def _cas_for(self, scope: WorkspaceScope) -> BlobCAS:
        return BlobCAS(
            self._object_store,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            local_cache_dir=self._cache_root / scope.tenant_id / scope.user_id,
        )

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        scope = spec.extra.get("workspace_scope")
        if not isinstance(scope, WorkspaceScope):
            raise ValueError(
                "StagedSandboxRuntime requires spec.extra['workspace_scope'] "
                "(a WorkspaceScope) — see tool.py"
            )
        for mount in spec.mounts:
            if not is_persistent_workspace(mount.scope.conversation_id):
                raise ValueError(f"only an agent's home or a group's drive can be mounted, not {mount.scope.conversation_id!r}")
        workspaces = [(None, spec.session_dir, scope), *((m.label, m.session_dir, m.scope) for m in spec.mounts)]
        # Taken in one fixed order, so two runs that open the same workspaces never each hold one the other is waiting for.
        async with AsyncExitStack() as held:
            for key in sorted({session_dir for _, session_dir, _ in workspaces}):
                await held.enter_async_context(self._lock_for(key))
            staged = [(label, session_dir, ws_scope, await self._stage_in_workspace(session_dir, ws_scope)) for label, session_dir, ws_scope in workspaces]
            result = await self._inner.execute(spec)
            problems: list[str] = []
            for label, session_dir, ws_scope, observed in staged:
                snapshot_id, unsaved = await self._stage_out_workspace(session_dir, ws_scope, observed)
                if label is None:
                    result.workspace_snapshot_id = snapshot_id
                problems += [f"{'' if label is None else f'groups/{label}/'}{p}" for p in unsaved]
            if problems:
                result.stderr += "\n[workspace] these files were not saved:\n" + "\n".join(f"  {p}" for p in problems) + "\n"
            return result

    async def stop(self) -> None:
        await self._inner.stop()

    # ── staging ──────────────────────────────────────────────────────────────

    def _scratch(self, session_dir: str) -> Path:
        scratch = (self._root / session_dir).resolve()
        if not scratch.is_relative_to(self._root):
            raise ValueError(f"session_dir escapes the scratch root: {session_dir!r}")
        return scratch

    async def _stage_in_workspace(self, session_dir: str, scope: WorkspaceScope) -> Any:
        """Bring one workspace into its scratch directory. Returns what ``_stage_out_workspace`` needs: the snapshot checked out, for a conversation's."""
        scratch_dir = self._scratch(session_dir)
        if is_persistent_workspace(scope.conversation_id):
            problems = await self._trees.stage_in(self._tree_prefix(scope), scratch_dir, session_dir)
            for problem in problems:
                logger.warning("stage-in %s: %s", session_dir, problem)
            return None
        return await self._stage_in(self._cas_for(scope), scope, scratch_dir)

    async def _stage_out_workspace(self, session_dir: str, scope: WorkspaceScope, observed: Any) -> tuple[str | None, list[str]]:
        """Save what the run changed in one workspace: ``(the new snapshot's id, if it is a conversation's; the files that were not saved)``."""
        scratch_dir = self._scratch(session_dir)
        if is_persistent_workspace(scope.conversation_id):
            try:
                return None, await self._trees.stage_out(self._tree_prefix(scope), scratch_dir, session_dir)
            except Exception as exc:  # noqa: BLE001 - the run's output still goes back to the agent
                logger.warning("stage-out %s failed: %s", session_dir, exc)
                return None, [f"(everything): could not be saved ({exc})"]
        return await self._stage_out(self._cas_for(scope), scope, scratch_dir, observed), []

    @staticmethod
    def _tree_prefix(scope: WorkspaceScope) -> str:
        # A home or a drive is never branched: it is always the one tree, whatever branch the conversation asking is on.
        return conversation_shared_prefix(scope.tenant_id, scope.user_id, scope.conversation_id, "main")

    async def _stage_in(
        self, cas: BlobCAS, scope: WorkspaceScope, scratch_dir: Path
    ) -> str | None:
        """Materialize the branch's current head snapshot into *scratch_dir*.

        Returns the checked-out snapshot's id (``None`` for a branch with no
        snapshot yet — a brand-new conversation/branch, left with an empty
        scratch dir, not an error) — this is what ``_stage_out`` uses as the
        real CAS parent, not a fresh re-read. Files already correctly
        materialized are copied from the CAS's local cache, not
        re-downloaded — see ``materialize.py``.
        """
        try:
            head = await self._workspace_store.get_branch_snapshot_head(
                scope.conversation_id, scope.branch_id
            )
        except Exception as exc:
            logger.warning(
                "Stage-in: could not read branch head for %s/%s: %s",
                scope.conversation_id,
                scope.branch_id,
                exc,
            )
            head = None
        if head is None:
            scratch_dir.mkdir(parents=True, exist_ok=True)
            return None
        if head.manifest is None:
            logger.warning(
                "Stage-in: snapshot %s uses manifest_ref, unsupported — "
                "leaving scratch empty",
                head.id,
            )
            scratch_dir.mkdir(parents=True, exist_ok=True)
            return None
        await materialize_manifest(cas, head.manifest, scratch_dir)
        return head.id

    async def _stage_out(
        self,
        cas: BlobCAS,
        scope: WorkspaceScope,
        scratch_dir: Path,
        observed_parent_id: str | None,
    ) -> str | None:
        """Hash whatever's in *scratch_dir* now and commit a new snapshot.

        Returns the new snapshot's id, or ``None`` if the commit failed or
        raced another writer on the same branch (``SnapshotConflictError``)
        — the run's stdout/output files are still returned to the user
        either way (never fail the tool call over this), but a raced
        commit's file changes are not durably recorded; retry is out of
        scope for v1 (no merge story — see the workspace plan).
        """
        try:
            new_snapshot = await commit_turn(
                self._workspace_store,
                cas,
                scratch_dir,
                session_id=scope.conversation_id,
                branch_id=scope.branch_id,
                expected_parent_id=observed_parent_id,
            )
            return new_snapshot.id
        except SnapshotConflictError as exc:
            logger.warning(
                "Stage-out: commit conflict on %s/%s: %s — run's file changes not saved",
                scope.conversation_id,
                scope.branch_id,
                exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Stage-out: commit failed for %s/%s: %s",
                scope.conversation_id,
                scope.branch_id,
                exc,
            )
            return None


__all__ = ["StagedSandboxRuntime"]
