"""Channels over the runtime's SQL tables: the sequencing, cursors and attention of
``substrate.runtime.channel``, in the same transactions as the inbox they wake."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from substrate.runtime.channel import (
    EVERYONE,
    AppendResult,
    ChannelChange,
    ChannelEntry,
    ChannelHead,
    ChannelObserver,
    EntryKind,
    Member,
    Mode,
    ParticipantKind,
    WakeReason,
)
from substrate.runtime.message import DataPayload, Message
from substrate.runtime.persistence.accounts import exhausted
from substrate.runtime.store import Delivery
from substrate.stores.database import Database, Row, Tx
from substrate.types.content import JsonObject
from substrate.types.identity import Actor
from substrate.types.ids import new_id

logger = logging.getLogger(__name__)

_PERSON = (ParticipantKind.HUMAN, ParticipantKind.EXTERNAL)
_WITH_INBOX = (ParticipantKind.AGENT, ParticipantKind.SYSTEM)

_SCHEMA_CHANNELS = """
CREATE TABLE IF NOT EXISTS rt_channels (
    channel TEXT PRIMARY KEY,
    tenant TEXT NOT NULL DEFAULT 'default',
    breaker INTEGER NOT NULL,
    next_seq INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rt_channel_members (
    channel TEXT NOT NULL,
    member TEXT NOT NULL,
    mode TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (channel, member)
);

CREATE TABLE IF NOT EXISTS rt_accounts (
    account TEXT PRIMARY KEY,
    tokens BIGINT NOT NULL DEFAULT 0,
    cost_micros BIGINT NOT NULL DEFAULT 0,
    turns BIGINT NOT NULL DEFAULT 0,
    max_tokens BIGINT,
    max_cost_micros BIGINT,
    max_turns BIGINT
);

CREATE TABLE IF NOT EXISTS rt_run_accounts (
    run_id TEXT NOT NULL,
    account TEXT NOT NULL,
    PRIMARY KEY (run_id, account)
);

CREATE TABLE IF NOT EXISTS rt_channel_entries (
    channel TEXT NOT NULL,
    seq INTEGER NOT NULL,
    sender TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    mentions_json TEXT NOT NULL,
    reply_to INTEGER,
    caused_by TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (channel, seq)
);
"""


def _schema_dedup(database: Database) -> str:
    """A repeated append with the same key lands once (``ctx.post`` across replays)."""
    exists = "IF NOT EXISTS " if database.dialect == "postgresql" else ""
    return (
        f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}dedup_key TEXT;\n"
        "CREATE UNIQUE INDEX IF NOT EXISTS rt_channel_entries_dedup_idx "
        "ON rt_channel_entries (channel, dedup_key) WHERE dedup_key IS NOT NULL;"
    )


def _schema_entry_data(database: Database) -> str:
    """What rides with an entry (attachments): kept as JSON text beside it."""
    exists = "IF NOT EXISTS " if database.dialect == "postgresql" else ""
    return f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}data_json TEXT;"


def _schema_engagement(database: Database) -> str:
    """How long a member follows a conversation it was drawn into, and until when it still does."""
    exists = "IF NOT EXISTS " if database.dialect == "postgresql" else ""
    return (
        f"ALTER TABLE rt_channels ADD COLUMN {exists}engage_s DOUBLE PRECISION NOT NULL DEFAULT 600;\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}engaged_until DOUBLE PRECISION;"
    )


def _schema_chat(database: Database) -> str:
    """People as members, ids, edits and reactions, receipts, and a breaker that counts depth rather than a streak.

    Existing rows keep working: every member so far was an agent woken at its own address, and each entry gets an id."""
    pg = database.dialect == "postgresql"
    exists = "IF NOT EXISTS " if pg else ""
    new_row_id = (
        "replace(gen_random_uuid()::text, '-', '')"
        if pg
        else "lower(hex(randomblob(16)))"
    )
    return (
        f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}id TEXT;\n"
        f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}cause_seq INTEGER;\n"
        f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}edited_at DOUBLE PRECISION;\n"
        f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}deleted_at DOUBLE PRECISION;\n"
        f"UPDATE rt_channel_entries SET id = {new_row_id} WHERE id IS NULL;\n"
        "CREATE UNIQUE INDEX IF NOT EXISTS rt_channel_entries_id_idx ON rt_channel_entries (id);\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}kind TEXT NOT NULL DEFAULT 'agent';\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}inbox TEXT;\n"
        "UPDATE rt_channel_members SET inbox = member WHERE inbox IS NULL;\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}delivered INTEGER NOT NULL DEFAULT -1;\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}joined_seq INTEGER NOT NULL DEFAULT 0;\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}exhausted_noticed INTEGER NOT NULL DEFAULT 0;\n"
        "CREATE TABLE IF NOT EXISTS rt_channel_reactions (\n"
        "    channel TEXT NOT NULL,\n"
        "    seq INTEGER NOT NULL,\n"
        "    participant TEXT NOT NULL,\n"
        "    emoji TEXT NOT NULL,\n"
        "    marker_seq INTEGER NOT NULL,\n"
        "    PRIMARY KEY (channel, seq, participant)\n"
        ");\n"
        f"ALTER TABLE rt_channels DROP COLUMN {'IF EXISTS ' if pg else ''}streak;"
    )


_SYSTEM = Actor("system", "channel")


def _when(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc)


def _entry(row: Row) -> ChannelEntry:
    return ChannelEntry(
        channel=row["channel"],
        seq=row["seq"],
        id=row["id"],
        sender=Actor.from_str(row["sender"]),
        kind=EntryKind(row["kind"]),
        text=row["text"],
        mentions=tuple(json.loads(row["mentions_json"])),
        reply_to=row["reply_to"],
        caused_by=row["caused_by"],
        cause_seq=row["cause_seq"],
        depth=row["depth"],
        data=json.loads(row["data_json"]) if row["data_json"] else {},
        at=datetime.fromtimestamp(row["at"], tz=timezone.utc),
        edited_at=_when(row["edited_at"]),
        deleted_at=_when(row["deleted_at"]),
    )


class Channels:
    """Mixed into ``RuntimeStore``; uses its transaction helper, clock and inbox delivery."""

    _clock: Callable[[], datetime]
    _tx: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]]
    _deliver: Callable[..., Awaitable[Any]]
    _channel_events: dict[str, asyncio.Event]
    _channel_observers: list[ChannelObserver]

    # ------------------------------------------------------------------ observers

    def channel_observe(self, observer: ChannelObserver) -> Callable[[], None]:
        self._channel_observers.append(observer)

        def stop() -> None:
            if observer in self._channel_observers:
                self._channel_observers.remove(observer)

        return stop

    async def _published(self, changes: Sequence[ChannelChange]) -> None:
        """After the commit: wake local waiters, then tell the observers. A failing observer must never fail the write that already landed."""
        for change in changes:
            if event := self._channel_events.get(change.channel):
                event.set()
            for observer in list(self._channel_observers):
                try:
                    await observer(change)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "channel observer failed for %s #%s", change.channel, change.seq
                    )

    # ------------------------------------------------------------------ channels and members

    async def channel_open(
        self,
        channel: str,
        *,
        tenant: str = "default",
        members: Sequence[Member] = (),
        breaker: int = 40,
        engage_s: float = 600.0,
    ) -> None:
        async def do(tx: Tx) -> None:
            await tx.lock(f"channel:{channel}")
            await tx.execute(
                "INSERT INTO rt_channels (channel, tenant, breaker, engage_s) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (channel) DO NOTHING",
                channel,
                tenant,
                breaker,
                engage_s,
            )
            for m in members:
                await self._set_member(tx, channel, m)

        await self._tx(do)

    async def channel_configure(
        self, channel: str, *, breaker: int | None = None, engage_s: float | None = None
    ) -> None:
        async def do(tx: Tx) -> None:
            await tx.lock(f"channel:{channel}")
            if breaker is not None:
                await tx.execute(
                    "UPDATE rt_channels SET breaker = ? WHERE channel = ?",
                    breaker,
                    channel,
                )
            if engage_s is not None:
                await tx.execute(
                    "UPDATE rt_channels SET engage_s = ? WHERE channel = ?",
                    engage_s,
                    channel,
                )

        await self._tx(do)

    @staticmethod
    async def _set_member(tx: Tx, channel: str, member: Member) -> None:
        inbox = member.inbox
        if inbox is None and member.kind in _WITH_INBOX:
            inbox = (
                member.agent
            )  # an agent is woken where it is addressed, unless it says otherwise
        await tx.execute(
            "INSERT INTO rt_channel_members (channel, member, mode, cursor, kind, inbox, joined_seq) "
            "VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT next_seq FROM rt_channels WHERE channel = ?), 0)) "
            "ON CONFLICT (channel, member) DO UPDATE SET mode = EXCLUDED.mode, kind = EXCLUDED.kind, inbox = EXCLUDED.inbox",
            channel,
            str(member.agent),
            member.mode.value,
            member.cursor,
            member.kind.value,
            None if inbox is None else str(inbox),
            channel,
        )

    async def channel_set_member(self, channel: str, member: Member) -> None:
        await self._tx(lambda tx: self._set_member(tx, channel, member))

    async def channel_remove_member(self, channel: str, agent: Actor) -> None:
        await self._tx(
            lambda tx: tx.execute(
                "DELETE FROM rt_channel_members WHERE channel = ? AND member = ?",
                channel,
                str(agent),
            )
        )

    async def channel_members(self, channel: str) -> list[Member]:
        async def do(tx: Tx) -> list[Member]:
            rows = await tx.fetchall(
                "SELECT member, mode, cursor, kind, inbox, delivered, joined_seq FROM rt_channel_members "
                "WHERE channel = ? ORDER BY member",
                channel,
            )
            return [
                Member(
                    agent=Actor.from_str(r["member"]),
                    mode=Mode(r["mode"]),
                    cursor=r["cursor"],
                    kind=ParticipantKind(r["kind"]),
                    inbox=Actor.from_str(r["inbox"]) if r["inbox"] else None,
                    delivered=r["delivered"],
                    joined_seq=r["joined_seq"],
                )
                for r in rows
            ]

        return await self._tx(do)

    # ------------------------------------------------------------------ reading

    async def channel_read(
        self, channel: str, *, after: int = -1, limit: int = 200
    ) -> list[ChannelEntry]:
        async def do(tx: Tx) -> list[ChannelEntry]:
            rows = await tx.fetchall(
                "SELECT * FROM rt_channel_entries WHERE channel = ? AND seq > ? ORDER BY seq LIMIT ?",
                channel,
                after,
                limit,
            )
            return [_entry(r) for r in rows]

        return await self._tx(do)

    async def channel_last(self, channel: str, limit: int = 1) -> list[ChannelEntry]:
        async def do(tx: Tx) -> list[ChannelEntry]:
            rows = await tx.fetchall(
                "SELECT * FROM rt_channel_entries WHERE channel = ? ORDER BY seq DESC LIMIT ?",
                channel,
                limit,
            )
            return [_entry(r) for r in reversed(rows)]

        return await self._tx(do)

    async def channel_read_before(
        self, channel: str, before: int, limit: int = 50
    ) -> list[ChannelEntry]:
        async def do(tx: Tx) -> list[ChannelEntry]:
            rows = await tx.fetchall(
                "SELECT * FROM rt_channel_entries WHERE channel = ? AND seq < ? ORDER BY seq DESC LIMIT ?",
                channel,
                before,
                limit,
            )
            return [_entry(r) for r in reversed(rows)]

        return await self._tx(do)

    async def channel_wait(self, channel: str, after: int, timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        while True:
            (last,) = await self.channel_last(channel, 1) or [None]
            if last is not None and last.seq > after:
                return True
            left = end - loop.time()
            if left <= 0:
                return False
            event = self._channel_events.setdefault(channel, asyncio.Event())
            event.clear()
            # Another process's append cannot set this event, so look again every second.
            try:
                await asyncio.wait_for(event.wait(), timeout=min(left, 1.0))
            except asyncio.TimeoutError:
                pass

    async def channel_heads(
        self, channels: Sequence[str], participant: Actor
    ) -> list[ChannelHead]:
        if not channels:
            return []
        me = str(participant)
        marks = ", ".join("?" for _ in channels)

        async def do(tx: Tx) -> list[ChannelHead]:
            cursors = {
                r["channel"]: r["cursor"]
                for r in await tx.fetchall(
                    f"SELECT channel, cursor FROM rt_channel_members WHERE member = ? AND channel IN ({marks})",
                    me,
                    *channels,
                )
            }
            if not cursors:
                return []
            unread = {
                r["channel"]: int(r["n"])
                for r in await tx.fetchall(
                    "SELECT e.channel AS channel, COUNT(*) AS n FROM rt_channel_entries e "
                    "JOIN rt_channel_members m ON m.channel = e.channel AND m.member = ? "
                    f"WHERE e.channel IN ({marks}) AND e.kind = 'message' AND e.deleted_at IS NULL "
                    "AND e.seq > m.cursor AND e.sender != ? GROUP BY e.channel",
                    me,
                    *channels,
                    me,
                )
            }
            latest = {
                r["channel"]: _entry(r)
                for r in await tx.fetchall(
                    f"SELECT e.* FROM rt_channel_entries e WHERE e.channel IN ({marks}) AND e.kind IN ('message', 'system') "
                    "AND e.seq = (SELECT MAX(x.seq) FROM rt_channel_entries x WHERE x.channel = e.channel "
                    "AND x.kind IN ('message', 'system'))",
                    *channels,
                )
            }
            return [
                ChannelHead(
                    channel=c,
                    latest=latest.get(c),
                    unread=unread.get(c, 0),
                    cursor=cursors[c],
                )
                for c in channels
                if c in cursors
            ]

        return await self._tx(do)

    async def channel_delete(self, channel: str) -> None:
        async def do(tx: Tx) -> None:
            await tx.lock(f"channel:{channel}")
            for table in (
                "rt_channel_reactions",
                "rt_channel_entries",
                "rt_channel_members",
                "rt_channels",
            ):
                await tx.execute(f"DELETE FROM {table} WHERE channel = ?", channel)
            await tx.execute(
                "DELETE FROM rt_accounts WHERE account = ?", f"channel:{channel}"
            )

        await self._tx(do)

    async def channel_mark_read(self, channel: str, agent: Actor, upto: int) -> None:
        await self._tx(
            lambda tx: tx.execute(
                "UPDATE rt_channel_members SET cursor = ? WHERE channel = ? AND member = ? AND cursor < ?",
                upto,
                channel,
                str(agent),
                upto,
            )
        )

    async def channel_mark_delivered(
        self, channel: str, participant: Actor, upto: int
    ) -> None:
        await self._tx(
            lambda tx: tx.execute(
                "UPDATE rt_channel_members SET delivered = ? WHERE channel = ? AND member = ? AND delivered < ?",
                upto,
                channel,
                str(participant),
                upto,
            )
        )

    # ------------------------------------------------------------------ writing

    async def channel_append(
        self,
        channel: str,
        *,
        sender: Actor,
        text: str,
        mentions: Sequence[str] = (),
        reply_to: int | None = None,
        caused_by: str | None = None,
        read_up_to: int | None = None,
        kind: EntryKind = EntryKind.MESSAGE,
        dedup_key: str | None = None,
        data: JsonObject | None = None,
        cause_seq: int | None = None,
        fresh: bool = False,
    ) -> AppendResult:
        async def do(tx: Tx) -> tuple[AppendResult, list[ChannelChange]]:
            await tx.lock(f"channel:{channel}")
            changes: list[ChannelChange] = []
            ch = await tx.fetchone(
                "SELECT * FROM rt_channels WHERE channel = ?", channel
            )
            if ch is None:
                raise KeyError(channel)
            latest = ch["next_seq"] - 1
            if dedup_key is not None:
                done = await tx.fetchone(
                    "SELECT seq, id FROM rt_channel_entries WHERE channel = ? AND dedup_key = ?",
                    channel,
                    dedup_key,
                )
                if done is not None:
                    return AppendResult(
                        seq=done["seq"], id=done["id"], latest=latest
                    ), changes
            member = await tx.fetchone(
                "SELECT kind FROM rt_channel_members WHERE channel = ? AND member = ?",
                channel,
                str(sender),
            )
            # A person is whoever the channel says is one; a sender it does not know is a person only if it is a ``user``.
            person = (
                ParticipantKind(member["kind"]) in _PERSON
                if member is not None
                else sender.type == "user"
            )
            by_agent = not person
            chained = (
                by_agent and not fresh
            )  # a fresh start (a timer fired) is not an answer to anything
            if chained and ch["paused"]:
                return AppendResult(paused=True, latest=latest), changes
            if read_up_to is not None and await tx.fetchone(
                "SELECT 1 AS x FROM rt_channel_entries WHERE channel = ? AND seq > ? AND sender != ? AND kind = 'message'",
                channel,
                read_up_to,
                str(sender),
            ):
                return AppendResult(stale=True, latest=latest), changes
            depth = 0
            if chained:
                depth = await self._cause_depth(tx, channel, cause_seq) + 1
            if chained and depth > ch["breaker"]:
                notice = await self._write_marker(
                    tx,
                    channel,
                    ch["next_seq"],
                    _SYSTEM,
                    EntryKind.SYSTEM,
                    f"Paused: agents have spoken {depth - 1} times in a row without a person. "
                    "A new message from a person resumes the conversation.",
                    None,
                )
                changes.append(notice)
                await tx.execute(
                    "UPDATE rt_channels SET paused = 1 WHERE channel = ?", channel
                )
                return AppendResult(paused=True, latest=notice.seq), changes
            seq = ch["next_seq"]
            entry_id = new_id()
            await self._write_entry(
                tx,
                channel,
                seq,
                sender,
                kind,
                text,
                tuple(mentions),
                reply_to,
                caused_by,
                depth,
                dedup_key,
                data,
                entry_id=entry_id,
                cause_seq=cause_seq,
            )
            changes.append(
                ChannelChange(
                    channel=channel, seq=seq, id=entry_id, kind=kind, sender=sender
                )
            )
            await tx.execute(
                "UPDATE rt_channels SET next_seq = ?, paused = ? WHERE channel = ?",
                seq + 1,
                0 if not by_agent else ch["paused"],
                channel,
            )
            await tx.execute(
                "UPDATE rt_channel_members SET cursor = ? WHERE channel = ? AND member = ? AND cursor < ?",
                seq,
                channel,
                str(sender),
                seq,
            )
            replied_to = None
            if reply_to is not None:
                row = await tx.fetchone(
                    "SELECT sender FROM rt_channel_entries WHERE channel = ? AND seq = ?",
                    channel,
                    reply_to,
                )
                replied_to = row["sender"] if row else None
            woken: list[Actor] = []
            now = self._clock().timestamp()
            window = now + ch["engage_s"]
            # What it is to speak is to be part of the conversation that follows.
            await tx.execute(
                "UPDATE rt_channel_members SET engaged_until = ? WHERE channel = ? AND member = ?",
                window,
                channel,
                str(sender),
            )
            members = await tx.fetchall(
                "SELECT member, mode, inbox, engaged_until, exhausted_noticed FROM rt_channel_members "
                "WHERE channel = ? AND member != ? ORDER BY member",
                channel,
                str(sender),
            )
            unannounced: list[
                str
            ] = []  # members whose budget has run out and who have not been told so yet
            for m in members:
                if m["inbox"] is None:
                    continue  # a person: it reads this when it opens the channel, and is never woken
                address = m["member"]
                reason = _wake_reason(
                    Mode(m["mode"]),
                    address,
                    tuple(mentions),
                    replied_to,
                    engaged=(m["engaged_until"] or 0) > now,
                )
                if reason is None:
                    continue
                if reason is WakeReason.DIRECT:
                    await tx.execute(
                        "UPDATE rt_channel_members SET engaged_until = ? WHERE channel = ? AND member = ?",
                        window,
                        channel,
                        address,
                    )
                accounts = (
                    f"channel:{channel}",
                    f"agent:{address}",
                    f"tenant:{ch['tenant']}",
                )
                if await exhausted(tx, accounts):
                    if not m["exhausted_noticed"]:
                        unannounced.append(address)
                    continue
                if m["exhausted_noticed"]:
                    await tx.execute(
                        "UPDATE rt_channel_members SET exhausted_noticed = 0 WHERE channel = ? AND member = ?",
                        channel,
                        address,
                    )
                target = Actor.from_str(m["inbox"])
                await self._deliver(
                    tx,
                    Delivery(
                        agent=target,
                        msg=Message(
                            id=f"chan:{channel}:{seq}:{address}",
                            target=target,
                            sender=sender,
                            payload=DataPayload(
                                data={
                                    "channel": channel,
                                    "seq": seq,
                                    "reason": reason.value,
                                }
                            ),
                            correlation_id=f"chan:{channel}",
                        ),
                        tenant=ch["tenant"],
                        accounts=accounts,
                    ),
                    wake=True,
                )
                woken.append(target)
            last = seq
            if unannounced:
                # Said once, so the silence is not a mystery; the next message from a person does not repeat it.
                for address in unannounced:
                    await tx.execute(
                        "UPDATE rt_channel_members SET exhausted_noticed = 1 WHERE channel = ? AND member = ?",
                        channel,
                        address,
                    )
                names = [Actor.from_str(a).key.split("@")[0] or a for a in unannounced]
                notice = await self._write_marker(
                    tx,
                    channel,
                    seq + 1,
                    _SYSTEM,
                    EntryKind.SYSTEM,
                    f"{', '.join(names)} {'has' if len(names) == 1 else 'have'} used up the budget "
                    "and will not answer until it is topped up.",
                    None,
                    data={"budget_exhausted": unannounced},
                )
                changes.append(notice)
                last = notice.seq
            return AppendResult(
                seq=seq, id=entry_id, latest=last, woken=tuple(woken)
            ), changes

        result, changes = await self._tx(do)
        await self._published(changes)
        return result

    @staticmethod
    async def _cause_depth(tx: Tx, channel: str, cause_seq: int | None) -> int:
        """The depth of what a post answers: the entry named, else the latest message. 0 when there is nothing to answer."""
        if cause_seq is not None:
            row = await tx.fetchone(
                "SELECT depth FROM rt_channel_entries WHERE channel = ? AND seq = ?",
                channel,
                cause_seq,
            )
        else:
            row = await tx.fetchone(
                "SELECT depth FROM rt_channel_entries WHERE channel = ? AND kind = 'message' ORDER BY seq DESC LIMIT 1",
                channel,
            )
        return int(row["depth"]) if row else 0

    async def _write_marker(
        self,
        tx: Tx,
        channel: str,
        seq: int,
        sender: Actor,
        kind: EntryKind,
        text: str,
        reply_to: int | None,
        *,
        data: JsonObject | None = None,
    ) -> ChannelChange:
        """An entry that records something happened (an edit, a reaction, a notice): no wake, no cursor, no depth. ``seq`` must be the
        channel's next one; the channel's counter moves past it."""
        entry_id = new_id()
        await self._write_entry(
            tx,
            channel,
            seq,
            sender,
            kind,
            text,
            (),
            reply_to,
            None,
            0,
            None,
            data,
            entry_id=entry_id,
        )
        await tx.execute(
            "UPDATE rt_channels SET next_seq = ? WHERE channel = ?", seq + 1, channel
        )
        return ChannelChange(
            channel=channel, seq=seq, id=entry_id, kind=kind, sender=sender
        )

    async def _own_message(
        self, tx: Tx, channel: str, seq: int, sender: Actor
    ) -> tuple[Row, Row] | None:
        """The channel and the message ``seq``, if ``sender`` wrote it and it is still there to change."""
        ch = await tx.fetchone("SELECT * FROM rt_channels WHERE channel = ?", channel)
        row = await tx.fetchone(
            "SELECT * FROM rt_channel_entries WHERE channel = ? AND seq = ?",
            channel,
            seq,
        )
        if ch is None or row is None:
            return None
        if (
            row["kind"] != EntryKind.MESSAGE.value
            or row["sender"] != str(sender)
            or row["deleted_at"] is not None
        ):
            return None
        return ch, row

    async def channel_edit(
        self, channel: str, seq: int, sender: Actor, text: str
    ) -> int | None:
        async def do(tx: Tx) -> tuple[int | None, list[ChannelChange]]:
            await tx.lock(f"channel:{channel}")
            found = await self._own_message(tx, channel, seq, sender)
            if found is None:
                return None, []
            ch, row = found
            await tx.execute(
                "UPDATE rt_channel_entries SET text = ?, edited_at = ? WHERE channel = ? AND seq = ?",
                text,
                self._clock().timestamp(),
                channel,
                seq,
            )
            marker = await self._write_marker(
                tx,
                channel,
                ch["next_seq"],
                sender,
                EntryKind.EDIT,
                text,
                seq,
                data={"previous": row["text"]},
            )
            return marker.seq, [marker]

        result, changes = await self._tx(do)
        await self._published(changes)
        return result

    async def channel_tombstone(
        self, channel: str, seq: int, sender: Actor
    ) -> int | None:
        async def do(tx: Tx) -> tuple[int | None, list[ChannelChange]]:
            await tx.lock(f"channel:{channel}")
            found = await self._own_message(tx, channel, seq, sender)
            if found is None:
                return None, []
            ch, _ = found
            await tx.execute(
                "UPDATE rt_channel_entries SET text = '', data_json = NULL, mentions_json = '[]', deleted_at = ? "
                "WHERE channel = ? AND seq = ?",
                self._clock().timestamp(),
                channel,
                seq,
            )
            # What an edit kept of it goes too: a delete is the person taking it back, wherever it was written down.
            await tx.execute(
                "UPDATE rt_channel_entries SET text = '', data_json = NULL WHERE channel = ? AND kind = 'edit' AND reply_to = ?",
                channel,
                seq,
            )
            marker = await self._write_marker(
                tx, channel, ch["next_seq"], sender, EntryKind.TOMBSTONE, "", seq
            )
            return marker.seq, [marker]

        result, changes = await self._tx(do)
        await self._published(changes)
        return result

    async def channel_react(
        self, channel: str, seq: int, participant: Actor, emoji: str
    ) -> int | None:
        async def do(tx: Tx) -> tuple[int | None, list[ChannelChange]]:
            await tx.lock(f"channel:{channel}")
            ch = await tx.fetchone(
                "SELECT * FROM rt_channels WHERE channel = ?", channel
            )
            target = await tx.fetchone(
                "SELECT kind FROM rt_channel_entries WHERE channel = ? AND seq = ?",
                channel,
                seq,
            )
            if (
                ch is None
                or target is None
                or target["kind"] != EntryKind.MESSAGE.value
            ):
                return None, []
            me = str(participant)
            current = await tx.fetchone(
                "SELECT emoji, marker_seq FROM rt_channel_reactions WHERE channel = ? AND seq = ? AND participant = ?",
                channel,
                seq,
                me,
            )
            if (current["emoji"] if current else "") == emoji:
                return (
                    current["marker_seq"] if current else seq
                ), []  # nothing to change
            if emoji:
                await tx.execute(
                    "INSERT INTO rt_channel_reactions (channel, seq, participant, emoji, marker_seq) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (channel, seq, participant) DO UPDATE SET emoji = EXCLUDED.emoji, marker_seq = EXCLUDED.marker_seq",
                    channel,
                    seq,
                    me,
                    emoji,
                    ch["next_seq"],
                )
            else:
                await tx.execute(
                    "DELETE FROM rt_channel_reactions WHERE channel = ? AND seq = ? AND participant = ?",
                    channel,
                    seq,
                    me,
                )
            marker = await self._write_marker(
                tx, channel, ch["next_seq"], participant, EntryKind.REACTION, emoji, seq
            )
            return marker.seq, [marker]

        result, changes = await self._tx(do)
        await self._published(changes)
        return result

    async def channel_reactions(
        self, channel: str, seqs: Sequence[int]
    ) -> dict[int, dict[str, str]]:
        if not seqs:
            return {}
        marks = ", ".join("?" for _ in seqs)

        async def do(tx: Tx) -> dict[int, dict[str, str]]:
            found: dict[int, dict[str, str]] = {}
            for r in await tx.fetchall(
                f"SELECT seq, participant, emoji FROM rt_channel_reactions WHERE channel = ? AND seq IN ({marks}) ORDER BY seq, participant",
                channel,
                *seqs,
            ):
                found.setdefault(r["seq"], {})[r["participant"]] = r["emoji"]
            return found

        return await self._tx(do)

    async def _write_entry(
        self,
        tx: Tx,
        channel: str,
        seq: int,
        sender: Actor,
        kind: EntryKind,
        text: str,
        mentions: tuple[str, ...],
        reply_to: int | None,
        caused_by: str | None,
        depth: int,
        dedup_key: str | None = None,
        data: JsonObject | None = None,
        *,
        entry_id: str,
        cause_seq: int | None = None,
    ) -> None:
        await tx.execute(
            "INSERT INTO rt_channel_entries "
            "(channel, seq, id, sender, kind, text, mentions_json, reply_to, caused_by, cause_seq, depth, at, dedup_key, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            channel,
            seq,
            entry_id,
            str(sender),
            kind.value,
            text,
            json.dumps(list(mentions)),
            reply_to,
            caused_by,
            cause_seq,
            depth,
            self._clock().timestamp(),
            dedup_key,
            json.dumps(data) if data else None,
        )


def _wake_reason(
    mode: Mode,
    address: str,
    mentions: tuple[str, ...],
    replied_to: str | None,
    *,
    engaged: bool,
) -> WakeReason | None:
    """Why an entry should wake a member in ``mode``, or ``None`` for no wake. Cheap on purpose: no model runs to decide it."""
    named = address in mentions
    if mode is Mode.MUTED:
        return WakeReason.DIRECT if named else None
    if named or EVERYONE in mentions or replied_to == address:
        return WakeReason.DIRECT
    if engaged:
        return WakeReason.ENGAGED
    return WakeReason.AMBIENT if mode is Mode.ALL else None
