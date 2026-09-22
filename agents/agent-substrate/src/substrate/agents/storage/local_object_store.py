"""Workspace-backed file store — a tenant-scoped directory tree on shared storage (L2).

Server-side only: the tree lives at ``root`` (a local dir in monolith dev, a
docker-compose volume, or a k8s RWX PVC mount in production — never on the
end user's machine). Keys are POSIX-relative paths under
``tenants/{tenant_id}/`` — see ``agents/workspace/layout.py`` for the
canonical builders (``tenants/{tid}/users/{uid}/...`` for a user's own
uploads not tied to a conversation, ``tenants/{tid}/users/{uid}/conversations/{cid}/workspace/...``
for a conversation's shared/version files — nested under its owning user,
not a tenant-level sibling — and ``tenants/{tid}/knowledge/{kb}/...`` for
knowledge-base documents). Callers (routes) build keys from authenticated
identity via ``layout.py``, never from raw client input.

This is Phase 1 (single-tier): the filesystem tree IS the record, not a
cache in front of object storage. Quota enforcement here is soft/app-layer,
scoped per tenant (not per user): even though a conversation's files do
carry a user segment in their key (for real ownership resolution — see
``routes/workspace.py::_thread_owner``), quota stays metered per tenant, the
same coarser identity ``chat.py``'s daily-message-limit check already
prefers over a raw user id. This check protects against accidental runaway
usage, not a hostile actor with another path onto the same volume. The hard
isolation boundary against other tenants is the k8s ``subPath`` mount into
each sandbox pod (see
``capabilities/tools/code_interpreter/code_interpreter/sandbox_service.py``),
not this quota check.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


class WorkspaceQuotaExceededError(Exception):
    """Raised when a write would push a tenant's usage past their quota."""

    def __init__(self, tenant_id: str, used_bytes: int, quota_bytes: int) -> None:
        self.tenant_id = tenant_id
        self.used_bytes = used_bytes
        self.quota_bytes = quota_bytes
        super().__init__(
            f"Storage quota exceeded for tenant {tenant_id!r}: "
            f"{used_bytes} bytes used, {quota_bytes} byte quota"
        )


class WorkspacePathError(ValueError):
    """Raised when a key resolves outside the workspace root."""


_USAGE_CACHE_TTL = 30.0  # seconds


class WorkspaceFileStore:
    """Async file store backed by a plain directory tree.

    Duck-types the same shape as ``S3FileStore``/``InMemoryFileStore``:
    ``upload``/``download``/``delete``/``presign_url``/``connect``/``disconnect``,
    plus workspace-specific helpers (``usage_bytes``, ``list_prefix``)
    used by the workspace management API.
    """

    def __init__(self, root: str | Path, user_quota_bytes: int) -> None:
        self._root = Path(root).resolve()
        self._quota_bytes = user_quota_bytes
        self._usage_cache: dict[str, tuple[float, int]] = {}
        # Per-tenant quota overrides (admin storage API) — in-memory, seeded
        # from the ``workspace_quotas`` table at startup and kept live by
        # the admin route on every write. A plain dict, not the DB itself:
        # this store has no DB dependency by design (see module docstring),
        # and a single-process/single-replica deployment (this stack's
        # actual target) has no cross-process consistency to worry about.
        self._quota_overrides: dict[str, int] = {}

    async def connect(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    async def disconnect(self) -> None:
        pass

    def _resolve(self, key: str) -> Path:
        """Resolve *key* against the workspace root, rejecting traversal.

        Mirrors ``sandbox_runtime._resolve_workspace_path``'s
        realpath-and-commonpath check so both sides of the mount enforce the
        identical rule.
        """
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise WorkspacePathError(f"Invalid workspace key: {key!r}")
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise WorkspacePathError(f"Key escapes workspace root: {key!r}") from None
        return candidate

    async def exists(self, key: str) -> bool:
        """True if *key* resolves to an existing file within the workspace.

        Cheap point check (no directory walk) so callers can try an exact key
        before falling back to a broader search. ``async`` to match
        ``S3FileStore.exists``, which needs a real round trip."""
        try:
            return self._resolve(key).is_file()
        except WorkspacePathError:
            return False

    @staticmethod
    def _tenant_id_from_key(key: str) -> str | None:
        """The tenant a key belongs to — every key under the current layout
        starts ``tenants/{tenant_id}/...`` (see ``layout.py``); nothing else
        is a reliable identity to meter storage against (a conversation key
        carries no user segment at all)."""
        parts = Path(key).parts
        if len(parts) >= 2 and parts[0] == "tenants":
            return parts[1]
        return None

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        """Sum of file sizes under ``tenants/{tenant_id}``, cached briefly.

        Walking the filesystem is the source of truth — it counts files the
        sandbox created directly, not just ones written through ``upload()``.

        ``async`` despite doing no I/O await, so it matches ``S3FileStore``'s
        signature — the workspace API awaits this without caring which store
        backs it.
        """
        now = time.monotonic()
        cached = self._usage_cache.get(tenant_id)
        if not force and cached is not None and now - cached[0] < _USAGE_CACHE_TTL:
            return cached[1]

        tenant_root = self._root / "tenants" / tenant_id
        total = 0
        if tenant_root.is_dir():
            for dirpath, _dirnames, filenames in os.walk(tenant_root):
                for name in filenames:
                    try:
                        total += (Path(dirpath) / name).stat().st_size
                    except OSError:
                        continue
        self._usage_cache[tenant_id] = (now, total)
        return total

    def _invalidate_usage(self, tenant_id: str | None) -> None:
        if tenant_id is not None:
            self._usage_cache.pop(tenant_id, None)

    def effective_quota(self, tenant_id: str) -> int:
        """The quota that actually applies to *tenant_id* — their override if
        one is set, otherwise the global default."""
        return self._quota_overrides.get(tenant_id, self._quota_bytes)

    def set_quota_override(self, tenant_id: str, quota_bytes: int | None) -> None:
        """Set (or, with ``None``, clear) *tenant_id*'s quota override.

        Callers own persistence (the admin route writes/deletes the
        ``workspace_quotas`` row) — this only updates what ``upload()``
        actually enforces on the next call, live, no restart needed."""
        if quota_bytes is None:
            self._quota_overrides.pop(tenant_id, None)
        else:
            self._quota_overrides[tenant_id] = quota_bytes

    async def list_all_tenants(self) -> list[str]:
        """Tenant ids with a workspace directory, i.e. every tenant that has
        ever uploaded a file, run a session, or ingested a KB document."""
        tenants_root = self._root / "tenants"
        if not tenants_root.is_dir():
            return []
        return sorted(p.name for p in tenants_root.iterdir() if p.is_dir())

    async def list_conversations(self, tenant_id: str) -> list[tuple[str, int, int]]:
        """``(conversation_id, size_bytes, file_count)`` for every
        conversation workspace under ``tenants/{tenant_id}/users/*/
        conversations/`` — the admin storage drill-down.

        Conversations nest under their owning user, not directly under the
        tenant (see ``agents/workspace/layout.py``'s
        ``conversation_workspace_prefix``), so every user directory under
        the tenant must be walked, not just a single ``conversations/``
        that no longer exists at the tenant's top level. Conversation ids
        are thread UUIDs — globally unique, so no user segment is needed to
        disambiguate them in the result.
        """
        users_root = self._root / "tenants" / tenant_id / "users"
        if not users_root.is_dir():
            return []
        results: list[tuple[str, int, int]] = []
        for user_dir in sorted(p for p in users_root.iterdir() if p.is_dir()):
            conversations_root = user_dir / "conversations"
            if not conversations_root.is_dir():
                continue
            for conv_dir in sorted(
                p for p in conversations_root.iterdir() if p.is_dir()
            ):
                size = 0
                count = 0
                for dirpath, _dirnames, filenames in os.walk(conv_dir):
                    for name in filenames:
                        try:
                            size += (Path(dirpath) / name).stat().st_size
                        except OSError:
                            continue
                        count += 1
                results.append((conv_dir.name, size, count))
        return results

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        del content_type  # plain files on disk; no per-object content-type store
        path = self._resolve(key)

        tenant_id = self._tenant_id_from_key(key)
        if tenant_id is not None:
            existing_size = path.stat().st_size if path.exists() else 0
            used = await self.usage_bytes(tenant_id)
            quota = self.effective_quota(tenant_id)
            if used - existing_size + len(data) > quota:
                raise WorkspaceQuotaExceededError(tenant_id, used, quota)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        self._invalidate_usage(tenant_id)

    async def download(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise KeyError(f"Object not found: {key}") from None

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        tenant_id = self._tenant_id_from_key(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        else:
            self._invalidate_usage(tenant_id)
            # Prune now-empty parent directories up to (not including) the root.
            parent = path.parent
            while parent != self._root and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    async def delete_prefix(self, prefix: str) -> int:
        """Delete a complete, validated storage subtree for GDPR erasure."""
        root = self._resolve(prefix.rstrip("/") or prefix)
        if not root.exists():
            return 0
        if root.is_file():
            root.unlink()
            return 1
        deleted = 0
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
                deleted += 1
            elif path.is_dir():
                path.rmdir()
        root.rmdir()
        self._usage_cache.clear()
        return deleted

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        """Copy all files from source_prefix to dest_prefix (for branch workspace forking).

        Returns the number of files copied.
        """
        src_root = self._resolve(source_prefix.rstrip("/") or source_prefix)
        dest_root = self._resolve(dest_prefix.rstrip("/") or dest_prefix)

        if not src_root.exists() or not src_root.is_dir():
            return 0

        copied = 0
        tenant_id = self._tenant_id_from_key(dest_prefix)

        for src_path in src_root.rglob("*"):
            if src_path.is_file():
                rel = src_path.relative_to(src_root)
                dest_path = dest_root / rel
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                data = src_path.read_bytes()

                if tenant_id is not None:
                    used = await self.usage_bytes(tenant_id)
                    quota = self.effective_quota(tenant_id)
                    if used + len(data) > quota:
                        raise WorkspaceQuotaExceededError(tenant_id, used, quota)

                dest_path.write_bytes(data)
                copied += 1

        if tenant_id is not None:
            self._invalidate_usage(tenant_id)
        return copied


    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        del expires_in
        # No real URL — caller detects "workspace://" and falls back to
        # /files/{id}/download, same convention as InMemoryFileStore's
        # "memory://" sentinel.
        return f"workspace://{key}"

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(relative_key, size_bytes, mtime)`` for every file under
        *prefix* — the generic listing primitive every caller that used to
        list "a user's files" and filter should use instead now that a key
        no longer necessarily carries a user segment (see
        ``_tenant_id_from_key``'s docstring). ``async`` to match
        ``S3FileStore``.
        """
        try:
            root = self._resolve(prefix.rstrip("/") or prefix)
        except WorkspacePathError:
            return []
        results: list[tuple[str, int, float]] = []
        if not root.is_dir():
            return results
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    stat = full.stat()
                except OSError:
                    continue
                results.append(
                    (
                        full.relative_to(self._root).as_posix(),
                        stat.st_size,
                        stat.st_mtime,
                    )
                )
        return results
