"""ArtifactStore — OKF bundles in object storage, at two scopes.

Layout (see ``capabilities/storage/layout.py`` for the key builders)::

    tenants/{tid}/conversations/{cid}/artifacts/   ← session scope
    tenants/{tid}/users/{uid}/artifacts/           ← global scope

Both are OKF bundles (``okf.py``): one markdown file per concept plus the
reserved ``index.md`` and ``log.md``. Object storage is already
prefix-shaped, so a "directory of markdown files" needs no translation
layer, and a whole scope is erasable with a single ``delete_prefix``.

**Session vs global** mirrors the isolation Claude's project-scoped memory
uses: a conversation's artifacts never leak into another conversation.
Anything that should outlive one conversation gets ``promote()``d into the
user's global bundle — which is an explicit, auditable act recorded in
``log.md``, not an implicit copy.

**Sandbox output is not an artifact.** Files a code-interpreter run
produces stay ordinary workspace files; they enter a bundle only when
something deliberately promotes one, at which point the concept carries
``resource:`` pointing at the file's object key rather than duplicating its
bytes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol

from substrate.capabilities.artifacts.okf import (
    INDEX_FILENAME,
    LOG_FILENAME,
    RESERVED_FILENAMES,
    Concept,
    OKFParseError,
    parse,
    serialize,
    utc_now_iso,
)
from substrate.capabilities.artifacts.okf import human_actor as okf_human_actor
from substrate.capabilities.storage.layout import (
    conversation_artifacts_prefix,
    user_artifacts_prefix,
)
from substrate.logger import setup_logging

logger = setup_logging("substrate.capabilities.artifacts.store")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LEN = 60


class _BlobStore(Protocol):
    """The subset of the file-store surface this needs — structural, so both
    ``WorkspaceFileStore`` and ``S3FileStore`` satisfy it without a base class."""

    async def upload(self, key: str, data: bytes, *, content_type: str = ...) -> Any: ...
    async def download(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...
    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]: ...
    async def delete_prefix(self, prefix: str) -> int: ...


def slugify(text: str, *, fallback: str = "note") -> str:
    """Filesystem/URL-safe slug used as the concept's filename stem.

    The slug is the concept's identity within a bundle, so it must survive a
    round-trip through an object key: ASCII-fold, lowercase, collapse
    everything else to single hyphens.
    """
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")
    return (slug[:_MAX_SLUG_LEN].strip("-") or fallback)


@dataclass(frozen=True)
class ArtifactRef:
    """A concept plus where it lives — what listings and the API return."""

    slug: str
    scope: str  # "session" | "global"
    concept: Concept


class ArtifactStore:
    """Reads and writes OKF bundles for one tenant."""

    def __init__(self, store: _BlobStore) -> None:
        self._store = store

    @staticmethod
    def human_actor(user_id: str) -> str:
        """OKF actor string for a person. Exposed on the instance so callers
        that must not import this layer (see ``create``) can still spell the
        convention correctly instead of hand-formatting ``human:`` prefixes."""
        return okf_human_actor(user_id)

    # ---- key helpers ---------------------------------------------------

    def scope_prefix(
        self, tenant_id: str, *, user_id: str | None = None, conversation_id: str | None = None
    ) -> str:
        """Resolve a bundle prefix. Exactly one of *user_id* (global) or
        *conversation_id* (session) must be given."""
        if conversation_id and user_id:
            raise ValueError("pass either user_id or conversation_id, not both")
        if conversation_id:
            return conversation_artifacts_prefix(tenant_id, conversation_id)
        if user_id:
            return user_artifacts_prefix(tenant_id, user_id)
        raise ValueError("one of user_id or conversation_id is required")

    @staticmethod
    def _concept_key(prefix: str, slug: str) -> str:
        return f"{prefix}/{slug}.md"

    # ---- concept CRUD --------------------------------------------------

    async def save(self, prefix: str, slug: str, concept: Concept) -> str:
        """Write a concept and refresh the bundle index. Returns the slug."""
        key = self._concept_key(prefix, slug)
        await self._store.upload(
            key, serialize(concept).encode("utf-8"), content_type="text/markdown"
        )
        await self._rebuild_index(prefix)
        return slug

    async def create(
        self,
        prefix: str,
        *,
        concept_type: str = "Memory",
        title: str | None = None,
        description: str | None = None,
        body: str = "",
        tags: list[str] | None = None,
        resource: str | None = None,
        author: str | None = None,
    ) -> tuple[str, Concept]:
        """Build, slug and persist a concept in one call.

        Exists so callers outside ``capabilities/`` (the HTTP routes, the
        agent tool) never have to construct a ``Concept`` themselves — that
        would make them import this layer directly, which the project's
        import-linter contracts forbid.

        *author* is an OKF actor string (``human:<id>`` or
        ``<producer>/<version>``); it's recorded as both the generator and
        an initial verification, so a person-authored concept starts
        Human-reviewed while an agent-authored one starts Machine-confirmed.
        """
        concept = Concept(
            type=concept_type,
            title=title,
            description=description,
            body=body,
            tags=list(tags or []),
            resource=resource,
        )
        if author:
            concept.generated = {"by": author, "at": utc_now_iso()}
            concept.mark_verified_by(author)
        slug = await self.unique_slug(prefix, slugify(title or concept_type))
        await self.save(prefix, slug, concept)
        return slug, concept

    async def update(
        self,
        prefix: str,
        slug: str,
        *,
        title: str | None = None,
        description: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        editor: str | None = None,
    ) -> Concept | None:
        """Patch the given fields (``None`` means "leave alone") and record
        *editor* as a verifier. Returns ``None`` if the concept is missing."""
        concept = await self.get(prefix, slug)
        if concept is None:
            return None
        if title is not None:
            concept.title = title
        if description is not None:
            concept.description = description
        if body is not None:
            concept.body = body
        if tags is not None:
            concept.tags = list(tags)
        if status is not None:
            concept.status = status
        if editor:
            concept.mark_verified_by(editor)
        await self.save(prefix, slug, concept)
        return concept

    async def get(self, prefix: str, slug: str) -> Concept | None:
        key = self._concept_key(prefix, slug)
        try:
            raw = await self._store.download(key)
        except Exception:
            # Absent, or unreadable — both mean "no such concept" to a caller.
            return None
        try:
            return parse(raw.decode("utf-8"))
        except (OKFParseError, UnicodeDecodeError) as exc:
            logger.warning("Skipping malformed OKF concept %s: %s", key, exc)
            return None

    async def list(
        self,
        prefix: str,
        *,
        scope: str,
        concept_type: str | None = None,
        tags: list[str] | None = None,
        include_deprecated: bool = False,
    ) -> list[ArtifactRef]:
        """List a bundle's concepts, newest-written first.

        A malformed document is skipped with a warning rather than failing
        the listing — the spec requires consumers to tolerate imperfect
        bundles, and one bad file must not hide the rest.
        """
        entries = await self._store.list_prefix(f"{prefix}/")
        # list_prefix yields (key, size, mtime); order by mtime desc.
        ordered = sorted(entries, key=lambda e: e[2], reverse=True)

        refs: list[ArtifactRef] = []
        wanted_tags = {t.lower() for t in (tags or [])}
        for key, _size, _mtime in ordered:
            name = key.rsplit("/", 1)[-1]
            if name in RESERVED_FILENAMES or not name.endswith(".md"):
                continue
            slug = name[: -len(".md")]
            concept = await self.get(prefix, slug)
            if concept is None:
                continue
            if not include_deprecated and concept.status == "deprecated":
                continue
            if concept_type and concept.type != concept_type:
                continue
            if wanted_tags and not wanted_tags.issubset({t.lower() for t in concept.tags}):
                continue
            refs.append(ArtifactRef(slug=slug, scope=scope, concept=concept))
        return refs

    async def delete(self, prefix: str, slug: str) -> bool:
        """Hard-delete. Prefer ``deprecate`` — see the module docstring."""
        key = self._concept_key(prefix, slug)
        if not await self._store.exists(key):
            return False
        await self._store.delete(key)
        await self._rebuild_index(prefix)
        return True

    async def deprecate(self, prefix: str, slug: str) -> bool:
        """Invalidate in place, keeping the document and its history."""
        concept = await self.get(prefix, slug)
        if concept is None:
            return False
        concept.deprecate()
        await self.save(prefix, slug, concept)
        await self._append_log(prefix, f"deprecated `{slug}`")
        return True

    async def clear(self, prefix: str) -> int:
        """Erase an entire bundle — one object-storage prefix delete."""
        return await self._store.delete_prefix(f"{prefix}/")

    # ---- promotion -----------------------------------------------------

    async def promote(
        self, session_prefix: str, global_prefix: str, slug: str
    ) -> str | None:
        """Copy a session concept into the user's global bundle.

        The session copy is left in place and marked deprecated rather than
        deleted, so the conversation's own history still reads correctly
        after promotion. Returns the slug in the global bundle, or ``None``
        if the source concept doesn't exist.
        """
        concept = await self.get(session_prefix, slug)
        if concept is None:
            return None

        target_slug = await self.unique_slug(global_prefix, slug)
        promoted = Concept(**{**concept.__dict__})
        promoted.extra = {**concept.extra, "promoted_from": f"{session_prefix}/{slug}.md"}
        await self.save(global_prefix, target_slug, promoted)
        await self._append_log(global_prefix, f"promoted `{target_slug}` from session")

        concept.deprecate()
        await self.save(session_prefix, slug, concept)
        return target_slug

    async def unique_slug(self, prefix: str, slug: str) -> str:
        """Avoid clobbering an unrelated global concept that happens to share
        a slug — suffix until free, the same way uploads uniquify filenames."""
        candidate = slug
        counter = 1
        while await self._store.exists(self._concept_key(prefix, candidate)):
            counter += 1
            candidate = f"{slug}-{counter}"
        return candidate

    # ---- reserved files ------------------------------------------------

    async def _rebuild_index(self, prefix: str) -> None:
        """Regenerate ``index.md``.

        The index is a convenience listing, not the source of truth — the
        spec makes it optional and forbids rejecting a bundle that lacks
        one. So a failure here is logged and swallowed: it must never make a
        successful concept write look like a failed one.
        """
        try:
            entries = await self._store.list_prefix(f"{prefix}/")
            lines = ["---", "type: Index", "title: Artifacts", "---", ""]
            rows: list[str] = []
            for key, _size, _mtime in sorted(entries, key=lambda e: e[0]):
                name = key.rsplit("/", 1)[-1]
                if name in RESERVED_FILENAMES or not name.endswith(".md"):
                    continue
                slug = name[: -len(".md")]
                concept = await self.get(prefix, slug)
                if concept is None:
                    continue
                label = concept.title or slug
                summary = f" — {concept.description}" if concept.description else ""
                rows.append(f"- [{label}](./{name}) `{concept.type}`{summary}")
            lines.extend(rows or ["_No artifacts yet._"])
            await self._store.upload(
                f"{prefix}/{INDEX_FILENAME}",
                ("\n".join(lines) + "\n").encode("utf-8"),
                content_type="text/markdown",
            )
        except Exception as exc:  # noqa: BLE001 - index is advisory, never fatal
            logger.warning("Failed to rebuild artifact index at %s: %s", prefix, exc)

    async def _append_log(self, prefix: str, message: str) -> None:
        """Append one line to the reserved ``log.md`` update history.

        Advisory like the index: promotion/deprecation already succeeded by
        the time this runs, so a write failure is logged, not raised.
        """
        key = f"{prefix}/{LOG_FILENAME}"
        try:
            try:
                existing = (await self._store.download(key)).decode("utf-8")
            except Exception:
                existing = "---\ntype: Log\ntitle: Update history\n---\n\n"
            line = f"- {utc_now_iso()} — {message}\n"
            await self._store.upload(
                key, (existing + line).encode("utf-8"), content_type="text/markdown"
            )
        except Exception as exc:  # noqa: BLE001 - history is advisory, never fatal
            logger.warning("Failed to append artifact log at %s: %s", prefix, exc)


__all__ = ["ArtifactRef", "ArtifactStore", "slugify"]
