"""Budget-account tests of the ``RuntimeStore`` conformance suite (mixed into ``RuntimeStoreConformance``).

A run is charged to its execution tree and to every named account it carries — a channel,
an agent, a tenant — so a conversation, which is a cycle across many trees, can be capped."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from substrate.runtime.channel import Member
from substrate.runtime.message import DataPayload, Message
from substrate.runtime.store import (
    Commit,
    NewEntry,
    RunSpec,
    SpawnSpec,
    Spend,
)
from substrate.types.identity import Actor
from substrate.types.supervision import ExecutionBudget

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
AGENT = Actor("agent", "a")
CHILD = Actor("agent", "c")


def _call(tokens: int, key: str | None = None) -> NewEntry:
    return NewEntry(kind="llm.call", dedup_key=key, spend=Spend(tokens=tokens, turns=1))


class AccountTests:
    @pytest.fixture
    async def store(self):  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    async def test_a_calls_spend_is_charged_to_each_of_the_runs_accounts(self, store):
        await store.create_run(RunSpec(agent=AGENT, accounts=("chan:1", "tenant:t")))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=(_call(100),)))
        assert (await store.account_spend("chan:1")).tokens == 100
        assert (await store.account_spend("tenant:t")).tokens == 100
        assert (await store.account_spend("other")).tokens == 0

    async def test_a_replayed_call_is_charged_once(self, store):
        await store.create_run(RunSpec(agent=AGENT, accounts=("chan:1",)))
        lease = await self.lease_one(store)
        for _ in range(2):
            await store.commit(lease, Commit(entries=(_call(100, "llm:1"),)))
        assert (await store.account_spend("chan:1")).tokens == 100

    async def test_an_account_is_exhausted_when_it_reaches_a_limit(self, store):
        await store.account_limit("chan:1", ExecutionBudget(max_tokens=100))
        await store.create_run(RunSpec(agent=AGENT, accounts=("chan:1",)))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=(_call(99),)))
        assert await store.accounts_exhausted(["chan:1"]) is None
        await store.commit(lease, Commit(entries=(_call(1),)))
        assert await store.accounts_exhausted(["chan:1", "x"]) == "chan:1"
        assert await store.run_accounts_exhausted(lease.run_id) == "chan:1"
        assert await store.run_accounts_exhausted(lease.run_id, strict=True) is None

    async def test_an_account_without_a_limit_is_never_exhausted(self, store):
        await store.create_run(RunSpec(agent=AGENT, accounts=("chan:1",)))
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=(_call(10**9),)))
        assert await store.accounts_exhausted(["chan:1"]) is None

    async def test_a_child_is_charged_to_its_parents_accounts(self, store):
        await store.create_run(RunSpec(agent=AGENT, accounts=("chan:1",)))
        parent = await self.lease_one(store, worker="p")
        spawn = SpawnSpec(
            effect_id="e",
            child=RunSpec(agent=CHILD),
            boot=Message(
                target=CHILD,
                sender=Actor.system("t"),
                payload=DataPayload(data={}),
            ),
        )
        result = await store.commit(parent, Commit(spawns=(spawn,)))
        child = await self.lease_one(store, worker="c")
        assert child.run_id == result.spawned["e"].run_id
        await store.commit(child, Commit(entries=(_call(40),)))
        assert (await store.account_spend("chan:1")).tokens == 40

    async def test_a_channel_wake_carries_the_channel_agent_and_tenant_accounts(
        self, store
    ):
        member = Actor("agent", "scout@1")
        await store.channel_open("g", tenant="t1", members=[Member(agent=member)])
        await store.channel_append("g", sender=Actor("user", "u"), text="hi")
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=(_call(7),)))
        for account in ("channel:g", f"agent:{member}", "tenant:t1"):
            assert (await store.account_spend(account)).tokens == 7, account

    async def test_no_member_is_woken_once_an_account_of_the_wake_is_exhausted(
        self, store
    ):
        scout, quill = Actor("agent", "scout@1"), Actor("agent", "quill@1")
        await store.channel_open(
            "g", members=[Member(agent=scout), Member(agent=quill)]
        )
        await store.account_limit("channel:g", ExecutionBudget(max_tokens=10))
        await store.channel_append("g", sender=Actor("user", "u"), text="hi")
        lease = await self.lease_one(store)
        await store.commit(lease, Commit(entries=(_call(10),)))
        r = await store.channel_append("g", sender=Actor("user", "u"), text="again")
        assert r.woken == ()

    async def test_one_exhausted_agent_does_not_silence_the_others(self, store):
        scout, quill = Actor("agent", "scout@1"), Actor("agent", "quill@1")
        await store.channel_open(
            "g", members=[Member(agent=scout), Member(agent=quill)]
        )
        await store.account_limit(f"agent:{scout}", ExecutionBudget(max_tokens=1))
        await store.channel_append("g", sender=Actor("user", "u"), text="hi")
        leases = await store.lease(worker_id="w", capacity=2, lease_s=30, now=NOW)
        for lease in leases:
            if lease.agent == scout:
                await store.commit(lease, Commit(entries=(_call(5),)))
        r = await store.channel_append("g", sender=Actor("user", "u"), text="again")
        assert quill in r.woken and scout not in r.woken
