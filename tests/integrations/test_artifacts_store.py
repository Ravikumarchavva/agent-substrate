"""ArtifactStore — scoping, listing/filtering, and session→global promotion.

Backed by an in-memory blob store so these exercise the real key layout and
index/log maintenance without needing SeaweedFS.
"""

from __future__ import annotations

import time

from substrate.integrations.artifacts.okf import Concept
from substrate.integrations.artifacts.store import ArtifactStore

TENANT = "t1"
USER = "u1"
CONVERSATION = "c1"


class FakeBlobStore:
    """Minimal stand-in matching the store's structural _BlobStore protocol."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self._mtimes: dict[str, float] = {}
        # Monotonic counter rather than wall-clock: writes inside one test can
        # land in the same millisecond, which would make ordering ambiguous.
        self._clock = 0.0

    async def upload(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self._clock += 1.0
        self.objects[key] = data
        self._mtimes[key] = self._clock

    async def download(self, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        return [
            (k, len(v), self._mtimes.get(k, 0.0))
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

    async def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)


def _store() -> tuple[ArtifactStore, FakeBlobStore]:
    blob = FakeBlobStore()
    return ArtifactStore(blob), blob


def _memory(text: str, **kw) -> Concept:
    return Concept(type="Memory", title=text, body=text, **kw)


async def test_session_and_global_scopes_are_separate_prefixes():
    store, blob = _store()
    session = store.scope_prefix(TENANT, USER, conversation_id=CONVERSATION)
    global_ = store.scope_prefix(TENANT, user_id=USER)

    assert session == f"tenants/{TENANT}/users/{USER}/conversations/{CONVERSATION}/artifacts"
    assert global_ == f"tenants/{TENANT}/users/{USER}/artifacts"

    await store.save(session, "a", _memory("session fact"))
    await store.save(global_, "b", _memory("global fact"))

    # Isolation: neither scope sees the other's concepts.
    assert [r.slug for r in await store.list(session, scope="session")] == ["a"]
    assert [r.slug for r in await store.list(global_, scope="global")] == ["b"]


async def test_session_artifacts_live_outside_the_sandbox_mount():
    """The sandbox bind-mounts `.../workspace/shared`; artifacts must not be
    reachable from arbitrary sandboxed code, nor show up as user files."""
    store, _ = _store()
    session = store.scope_prefix(TENANT, USER, conversation_id=CONVERSATION)
    assert "/workspace/" not in f"{session}/"


async def test_reserved_files_are_never_listed_as_concepts():
    store, blob = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    await store.save(prefix, "real", _memory("real"))

    # save() writes index.md; both reserved names must stay out of listings.
    assert f"{prefix}/index.md" in blob.objects
    assert [r.slug for r in await store.list(prefix, scope="global")] == ["real"]


async def test_index_lists_concepts_with_type_and_description():
    store, blob = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    await store.save(
        prefix, "terse", Concept(type="Memory", title="Terse", description="No summaries.")
    )
    index = blob.objects[f"{prefix}/index.md"].decode()
    assert "[Terse](./terse.md)" in index
    assert "`Memory`" in index
    assert "No summaries." in index


async def test_listing_filters_by_type_and_tags():
    store, _ = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    await store.save(prefix, "m1", Concept(type="Memory", tags=["style", "comms"]))
    await store.save(prefix, "m2", Concept(type="Memory", tags=["style"]))
    await store.save(prefix, "f1", Concept(type="File", tags=["style"]))

    assert {r.slug for r in await store.list(prefix, scope="global", concept_type="Memory")} == {
        "m1",
        "m2",
    }
    # Tag filter is conjunctive: every requested tag must be present.
    assert {
        r.slug for r in await store.list(prefix, scope="global", tags=["style", "comms"])
    } == {"m1"}


async def test_deprecated_concepts_are_hidden_unless_requested():
    store, _ = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    await store.save(prefix, "old", _memory("lives in Hyderabad"))
    await store.deprecate(prefix, "old")

    assert await store.list(prefix, scope="global") == []
    shown = await store.list(prefix, scope="global", include_deprecated=True)
    assert [r.slug for r in shown] == ["old"]
    # Invalidated, not destroyed.
    assert "Hyderabad" in shown[0].concept.body


async def test_promote_copies_to_global_and_retires_the_session_copy():
    store, _ = _store()
    session = store.scope_prefix(TENANT, USER, conversation_id=CONVERSATION)
    global_ = store.scope_prefix(TENANT, user_id=USER)
    await store.save(session, "fact", _memory("prefers dark mode"))

    slug = await store.promote(session, global_, "fact")
    assert slug == "fact"

    promoted = await store.get(global_, "fact")
    assert promoted is not None and "dark mode" in promoted.body
    # Provenance survives the move.
    assert promoted.extra["promoted_from"].endswith("/fact.md")

    # Session copy is retired, not deleted, so the conversation still reads back.
    session_copy = await store.get(session, "fact")
    assert session_copy is not None
    assert session_copy.status == "deprecated"


async def test_promote_does_not_clobber_an_existing_global_slug():
    store, _ = _store()
    session = store.scope_prefix(TENANT, USER, conversation_id=CONVERSATION)
    global_ = store.scope_prefix(TENANT, user_id=USER)
    await store.save(global_, "fact", _memory("existing global fact"))
    await store.save(session, "fact", _memory("different session fact"))

    slug = await store.promote(session, global_, "fact")
    assert slug == "fact-2"
    original = await store.get(global_, "fact")
    assert original is not None and "existing global" in original.body


async def test_promote_missing_concept_returns_none():
    store, _ = _store()
    session = store.scope_prefix(TENANT, USER, conversation_id=CONVERSATION)
    global_ = store.scope_prefix(TENANT, user_id=USER)
    assert await store.promote(session, global_, "nope") is None


async def test_promotion_is_recorded_in_the_log():
    store, blob = _store()
    session = store.scope_prefix(TENANT, USER, conversation_id=CONVERSATION)
    global_ = store.scope_prefix(TENANT, user_id=USER)
    await store.save(session, "fact", _memory("x"))
    await store.promote(session, global_, "fact")

    log = blob.objects[f"{global_}/log.md"].decode()
    assert "promoted `fact` from session" in log


async def test_malformed_concept_is_skipped_not_fatal():
    """Spec requires tolerant consumption — one bad file must not hide the rest."""
    store, blob = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    await store.save(prefix, "good", _memory("fine"))
    blob.objects[f"{prefix}/broken.md"] = b"no frontmatter at all"
    blob._mtimes[f"{prefix}/broken.md"] = time.time()

    assert [r.slug for r in await store.list(prefix, scope="global")] == ["good"]


async def test_clear_erases_the_whole_bundle():
    store, blob = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    await store.save(prefix, "a", _memory("a"))
    await store.save(prefix, "b", _memory("b"))

    removed = await store.clear(prefix)
    assert removed >= 2
    assert not [k for k in blob.objects if k.startswith(f"{prefix}/")]


async def test_delete_returns_false_for_unknown_slug():
    store, _ = _store()
    prefix = store.scope_prefix(TENANT, user_id=USER)
    assert await store.delete(prefix, "ghost") is False
