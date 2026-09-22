"""StagedSandboxRuntime — materialize a branch's workspace before a run, commit it after.

Every runtime — nsjail bind-mounting a directory, an in-pod k8s server writing
to its own local disk, the inprocess test runtime — executes against a real
local filesystem tree. But the durable source of truth is no longer a
filesystem at all: it's a content-addressed manifest (``agents/workspace/``).
This wrapper is the one place that bridges the two: before a run it
materializes the branch's current snapshot into local scratch (hardlinking
from the CAS's local cache where possible — see
``agents/workspace/materialize.py``), and after a run it hashes whatever
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
before), not yet per-branch — see ``infrastructure/serving_factory.py``'s
runtime wiring for where that's enforced.

Scratch is disposable by construction: anything a run needs is checked out
from the workspace store, and anything worth keeping is committed back. That
makes it safe for the local tree to be a container's ephemeral disk.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from substrate.agents.workspace.cas import BlobCAS
from substrate.agents.workspace.materialize import materialize as materialize_manifest
from substrate.agents.workspace.scope import WorkspaceScope
from substrate.agents.workspace.snapshots import commit_turn
from substrate.kernel.exceptions import SnapshotConflictError
from substrate.kernel.storage.objects import ObjectStore
from substrate.kernel.storage.snapshots import WorkspaceStore
from substrate.logger import setup_logging

from .base import ExecResult, SandboxSpec

logger = setup_logging("substrate.code_interpreter.staged")


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
        object_store: ObjectStore,
        workspace_store: WorkspaceStore,
        scratch_root: str | Path,
    ) -> None:
        self._inner = inner
        self._object_store = object_store
        self._workspace_store = workspace_store
        self._root = Path(scratch_root).resolve()
        self._cache_root = self._root / ".cas-cache"
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
        async with self._lock_for(spec.session_dir):
            cas = self._cas_for(scope)
            scratch_dir = self._root / spec.session_dir
            observed_parent_id = await self._stage_in(cas, scope, scratch_dir)
            result = await self._inner.execute(spec)
            result.workspace_snapshot_id = await self._stage_out(
                cas, scope, scratch_dir, observed_parent_id
            )
            return result

    async def stop(self) -> None:
        await self._inner.stop()

    # ── staging ──────────────────────────────────────────────────────────────

    async def _stage_in(
        self, cas: BlobCAS, scope: WorkspaceScope, scratch_dir: Path
    ) -> str | None:
        """Materialize the branch's current head snapshot into *scratch_dir*.

        Returns the checked-out snapshot's id (``None`` for a branch with no
        snapshot yet — a brand-new conversation/branch, left with an empty
        scratch dir, not an error) — this is what ``_stage_out`` uses as the
        real CAS parent, not a fresh re-read. Files already correctly
        materialized are hardlinked from the CAS's local cache, not
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
