"""Conformance suite for ``MemoryStore``.

Every implementation of the port — the local filesystem default, the Postgres store, the
Lance store, any a consumer writes — must pass exactly these tests. They are the tenancy
guarantees: what one tenant, user or session can and cannot see of another's records.

Subclass it and provide the ``store`` fixture. ``residue`` may be overridden to look for
bytes of an erased record in the store's own storage, which is the only way to check that
an erasure left nothing behind rather than merely hiding it::

    class TestMyStore(MemoryStoreConformance):
        @pytest.fixture
        async def store(self): ...
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from substrate.types.errors import ScopeViolationError
from substrate.stores.memory import (
    MemoryCategory,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    TenantWide,
)

ACME = "acme"
EVIL = "evilcorp"


def ns(
    tenant: str = ACME,
    user: str | None = None,
    agent: str | None = None,
    session: str | None = None,
) -> MemoryNamespace:
    return MemoryNamespace(
        tenant_id=tenant, user_id=user, agent_id=agent, session_id=session
    )


def rec(text: str, namespace: MemoryNamespace, **kwargs: object) -> MemoryRecord:
    return MemoryRecord.from_text(text, namespace=namespace, **kwargs)  # type: ignore[arg-type]


async def texts(
    store: MemoryStore, caller: MemoryNamespace, **query: object
) -> list[str]:
    return sorted(
        m.text
        for m in await store.query(MemoryQuery(namespace=caller, limit=100, **query))
    )  # type: ignore[arg-type]


class MemoryStoreConformance:
    """Subclass and provide ``store``; every ``test_*`` here then runs against it."""

    @pytest.fixture
    async def store(self) -> MemoryStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    async def residue(self, store: MemoryStore, needle: str) -> list[str]:
        """Where ``needle`` can still be found in the store's own storage. Empty by default:
        a store that can inspect its files or rows overrides this."""
        return []

    # ==================================================================== round trip

    async def test_a_saved_record_is_returned_to_its_owner(
        self, store: MemoryStore
    ) -> None:
        owner = ns(user="alice")
        record = rec("alice likes tea", owner, category=MemoryCategory.DIRECTIVE)
        assert await store.save(record) == record.id
        got = await store.get(owner, record.id)
        assert (
            got is not None
            and got.text == "alice likes tea"
            and got.category == MemoryCategory.DIRECTIVE
        )
        assert got.namespace == owner

    async def test_saving_again_replaces_the_record(self, store: MemoryStore) -> None:
        owner = ns(user="alice")
        first = rec("v1", owner)
        await store.save(first)
        await store.save(rec("v2", owner, id=first.id))
        assert (await store.get(owner, first.id)).text == "v2"
        assert await texts(store, owner) == ["v2"]

    async def test_a_missing_record_is_none_and_deleting_it_is_false(
        self, store: MemoryStore
    ) -> None:
        assert await store.get(ns(), "nope") is None
        assert await store.delete(ns(), "nope") is False

    async def test_delete_removes_the_record(self, store: MemoryStore) -> None:
        owner = ns(user="alice")
        record = rec("x", owner)
        await store.save(record)
        assert await store.delete(owner, record.id) is True
        assert await store.get(owner, record.id) is None

    # ==================================================================== tenancy (I3)

    async def test_i03_another_tenant_cannot_read_a_record_by_its_id(
        self, store: MemoryStore
    ) -> None:
        record = rec("alice's medical note", ns(user="alice"))
        await store.save(record)
        assert await store.get(ns(EVIL, user="alice"), record.id) is None
        assert await store.get(ns(EVIL), record.id) is None

    async def test_i03_another_tenant_cannot_delete_a_record_by_its_id(
        self, store: MemoryStore
    ) -> None:
        owner = ns(user="alice")
        record = rec("keep me", owner)
        await store.save(record)
        assert await store.delete(ns(EVIL, user="alice"), record.id) is False
        assert await store.get(owner, record.id) is not None

    async def test_i03_another_tenant_cannot_touch_a_record(
        self, store: MemoryStore
    ) -> None:
        owner = ns(user="alice")
        record = rec("x", owner)
        await store.save(record)
        await store.touch(ns(EVIL, user="alice"), [record.id])
        assert (await store.get(owner, record.id)).access_count == 0

    async def test_i03_a_record_with_a_colliding_id_in_another_tenant_is_a_different_record(
        self, store: MemoryStore
    ) -> None:
        """Ids are caller-supplied. Saving under someone else's id must neither overwrite their
        record nor reveal it — the two records simply coexist."""
        original = rec("acme's fact", ns(user="alice"))
        await store.save(original)
        await store.save(
            rec("attacker content", ns(EVIL, user="mallory"), id=original.id)
        )

        assert (await store.get(ns(user="alice"), original.id)).text == "acme's fact"
        assert (
            await store.get(ns(EVIL, user="mallory"), original.id)
        ).text == "attacker content"

    async def test_i03_another_user_of_the_same_tenant_cannot_take_over_a_record(
        self, store: MemoryStore
    ) -> None:
        original = rec("alice's fact", ns(user="alice"))
        await store.save(original)
        with pytest.raises(ScopeViolationError):
            await store.save(
                rec("mallory's content", ns(user="mallory"), id=original.id)
            )
        assert (await store.get(ns(user="alice"), original.id)).text == "alice's fact"

    async def test_a_user_cannot_delete_a_tenant_level_record_but_the_tenant_itself_can(
        self, store: MemoryStore
    ) -> None:
        shared = rec("office closes at 5", ns())
        await store.save(shared)
        assert await store.delete(ns(user="alice"), shared.id) is False, (
            "a user deleted the whole organisation's fact"
        )
        assert await store.get(ns(user="alice"), shared.id) is not None
        assert await store.delete(ns(), shared.id) is True

    async def test_i03_a_user_does_not_see_another_users_record(
        self, store: MemoryStore
    ) -> None:
        record = rec("alice's secret", ns(user="alice"))
        await store.save(record)
        assert await store.get(ns(user="bob"), record.id) is None
        assert await texts(store, ns(user="bob")) == []

    async def test_i03_a_tenant_level_record_is_visible_to_every_user_of_the_tenant(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("office closes at 5", ns()))
        assert await texts(store, ns(user="alice")) == ["office closes at 5"]
        assert await texts(store, ns(user="bob")) == ["office closes at 5"]
        assert await texts(store, ns(EVIL, user="alice")) == []

    async def test_i03_a_session_record_is_visible_only_inside_its_session(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("scratch", ns(user="alice", session="s1")))
        assert await texts(store, ns(user="alice", session="s1")) == ["scratch"]
        assert await texts(store, ns(user="alice", session="s2")) == []
        assert await texts(store, ns(user="alice")) == []

    async def test_i03_omitting_a_field_never_widens_the_query(
        self, store: MemoryStore
    ) -> None:
        """The dangerous default this replaces: a query that merely left out ``user_id`` read
        every user in the tenant. Leaving a field out now narrows."""
        await store.save(rec("alice's secret", ns(user="alice")))
        await store.save(rec("bob's secret", ns(user="bob")))
        assert await texts(store, ns()) == []

    async def test_a_tenant_wide_query_sees_every_user_of_that_tenant_and_no_other(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("a", ns(user="alice")))
        await store.save(rec("b", ns(user="bob")))
        await store.save(rec("c", ns(EVIL, user="mallory")))
        found = await texts(
            store,
            ns(),
            tenant_wide=TenantWide(reason="admin export for support ticket"),
        )
        assert found == ["a", "b"]

    def test_a_tenant_wide_request_must_say_why(self) -> None:
        with pytest.raises(ValidationError):
            TenantWide(reason="")

    def test_a_namespace_needs_a_tenant_and_no_empty_owner(self) -> None:
        with pytest.raises(ValidationError):
            MemoryNamespace(tenant_id="")
        with pytest.raises(ValidationError):
            MemoryNamespace(tenant_id="t", user_id="")

    # ==================================================================== erasure (I4)

    async def test_i04_erasing_a_user_removes_everything_of_theirs_and_nothing_else(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("alice secret one", ns(user="alice")))
        await store.save(
            rec("alice secret two", ns(user="alice", agent="bot", session="s1"))
        )
        await store.save(rec("bob keeps this", ns(user="bob")))
        await store.save(rec("shared office fact", ns()))
        await store.save(rec("other tenant alice", ns(EVIL, user="alice")))

        assert await store.erase(ns(user="alice")) == 2

        assert await texts(store, ns(user="alice", agent="bot", session="s1")) == [
            "shared office fact"
        ]
        assert await texts(store, ns(user="bob")) == [
            "bob keeps this",
            "shared office fact",
        ]
        assert await texts(store, ns(EVIL, user="alice")) == ["other tenant alice"]

    async def test_i04_erasing_a_tenant_removes_every_record_of_it(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("a", ns(user="alice")))
        await store.save(rec("b", ns()))
        await store.save(rec("c", ns(EVIL)))
        assert await store.erase(ns()) == 2
        assert (
            await texts(
                store, ns(user="alice"), tenant_wide=TenantWide(reason="verify erase")
            )
            == []
        )
        assert await texts(store, ns(EVIL)) == ["c"]

    async def test_i04_an_erased_record_leaves_no_bytes_behind(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("the-secret-needle", ns(user="alice")))
        await store.erase(ns(user="alice"))
        assert await self.residue(store, "the-secret-needle") == []

    async def test_erasing_nothing_is_zero(self, store: MemoryStore) -> None:
        assert await store.erase(ns("no-such-tenant")) == 0

    # ==================================================================== queries

    async def test_a_text_query_matches_content(self, store: MemoryStore) -> None:
        owner = ns(user="alice")
        await store.save(rec("likes green tea", owner))
        await store.save(rec("works on rockets", owner))
        found = await texts(store, owner, text_query="tea")
        assert found == ["likes green tea"]

    async def test_a_query_filters_by_category_and_status(
        self, store: MemoryStore
    ) -> None:
        owner = ns(user="alice")
        await store.save(rec("rule", owner, category=MemoryCategory.DIRECTIVE))
        await store.save(rec("fact", owner, category=MemoryCategory.SEMANTIC))
        await store.save(rec("maybe", owner, status=MemoryStatus.CANDIDATE))
        assert await texts(store, owner, categories=[MemoryCategory.DIRECTIVE]) == [
            "rule"
        ]
        assert await texts(store, owner) == ["fact", "rule"], (
            "only active records by default"
        )
        assert await texts(store, owner, statuses=[MemoryStatus.CANDIDATE]) == ["maybe"]

    async def test_a_query_respects_its_limit(self, store: MemoryStore) -> None:
        owner = ns(user="alice")
        for i in range(5):
            await store.save(rec(f"fact {i}", owner))
        assert len(await store.query(MemoryQuery(namespace=owner, limit=3))) == 3

    async def test_touching_a_visible_record_counts_the_access(
        self, store: MemoryStore
    ) -> None:
        owner = ns(user="alice")
        record = rec("x", owner)
        await store.save(record)
        await store.touch(owner, [record.id])
        await store.touch(owner, [record.id])
        got = await store.get(owner, record.id)
        assert got.access_count == 2 and got.last_accessed_at is not None

    # ==================================================================== hostile ids

    @pytest.mark.parametrize(
        "hostile",
        [
            "x' OR '1'='1",
            "a'; DROP TABLE t; --",
            "../../etc/passwd",
            "a/b\\c",
            "..",
            "%2e%2e",
            "ünï-çødé",
            "x" * 200,
        ],
    )
    async def test_a_hostile_record_id_is_inert(
        self, store: MemoryStore, hostile: str
    ) -> None:
        owner = ns(user="alice")
        bystander = rec("bystander", ns(user="bob"))
        await store.save(bystander)
        await store.save(rec("mine", owner, id=hostile))

        assert (await store.get(owner, hostile)).text == "mine"
        assert await store.get(owner, "x") is None
        assert (await store.get(ns(user="bob"), bystander.id)).text == "bystander"
        assert await store.delete(owner, hostile) is True
        assert await store.get(ns(user="bob"), bystander.id) is not None

    async def test_a_hostile_tenant_or_user_name_is_inert(
        self, store: MemoryStore
    ) -> None:
        await store.save(rec("victim", ns("acme", user="alice")))
        attacker = ns("acme' OR '1'='1", user="x' OR '1'='1")
        await store.save(rec("attacker", attacker))
        assert await texts(store, attacker) == ["attacker"]
        assert await texts(store, ns("acme", user="alice")) == ["victim"]
        assert await store.erase(attacker) == 1
        assert await texts(store, ns("acme", user="alice")) == ["victim"]
