from __future__ import annotations

from datetime import UTC, datetime

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.runtime.channel import ChannelStore, EntryKind
from loamz.engine.agents.kernel.types.identity import Actor
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.directory.avatars.service import AvatarService, avatars
from loamz.server.agents.features.directory.pairs.service import channels
from loamz.server.agents.features.directory.pins.service import PinsService, pins
from loamz.server.agents.features.groups.groups.model import (
    GroupIn,
    GroupList,
    GroupOut,
    GroupPatch,
    MemberOut,
    MessageList,
    MessageOut,
)
from loamz.server.agents.features.groups.groups.store import GroupRecord, GroupsStore

PIN_KIND = "group"


class GroupsService:
    def __init__(
        self,
        container: Container,
        store: GroupsStore,
        pins_service: PinsService,
        avatars_service: AvatarService,
        channel_store: ChannelStore,
    ) -> None:
        self._c = container
        self._store = store
        self._pins = pins_service
        self._avatars = avatars_service
        self._channels = channel_store

    def _channel_name(self, group_id: str) -> str:
        return f"group/{group_id}"

    async def _to_out(self, caller: Caller, row: GroupRecord) -> GroupOut:
        members_data = await self._store.members(row.id)
        members: list[MemberOut] = [
            MemberOut(agent_id=m.agent_id, mode=m.mode) for m in members_data
        ]
        pinned_map = await self._pins.pinned_at(caller, PIN_KIND)
        return GroupOut(
            id=row.id,
            name=row.name,
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            updated_at=datetime.fromtimestamp(row.updated_at, UTC),
            members=members,
            avatar=row.avatar_key,
            pinned_at=pinned_map.get(row.id),
        )

    async def list(self, caller: Caller) -> GroupList:
        rows = await self._store.list(caller.tenant_id, caller.user_id)
        items = [await self._to_out(caller, r) for r in rows]
        return GroupList(items=items)

    async def get(self, caller: Caller, group_id: str) -> GroupOut:
        row = await self._store.get(caller.tenant_id, caller.user_id, group_id)
        if row is None:
            raise NotFoundError("Group not found")
        return await self._to_out(caller, row)

    async def create(self, caller: Caller, body: GroupIn) -> GroupOut:
        member_pairs = [(m.agent_id, m.mode) for m in body.members]
        row = await self._store.create(caller.tenant_id, caller.user_id, body.name, member_pairs)
        await self._channels.channel_open(self._channel_name(row.id), tenant=caller.tenant_id)
        return await self._to_out(caller, row)

    async def patch(self, caller: Caller, group_id: str, body: GroupPatch) -> GroupOut:
        row = await self._store.update(caller.tenant_id, caller.user_id, group_id, name=body.name)
        if row is None:
            raise NotFoundError("Group not found")
        return await self._to_out(caller, row)

    async def delete(self, caller: Caller, group_id: str) -> None:
        deleted = await self._store.delete(caller.tenant_id, caller.user_id, group_id)
        if not deleted:
            raise NotFoundError("Group not found")
        await self._channels.channel_delete(self._channel_name(group_id))

    async def set_avatar(self, caller: Caller, group_id: str, data: bytes) -> GroupOut:
        key = f"groups/{group_id}/avatar.png"
        await self._c.file_store.upload(key, data, content_type="image/png")
        row = await self._store.update(caller.tenant_id, caller.user_id, group_id, avatar_key=key)
        if row is None:
            raise NotFoundError("Group not found")
        return await self._to_out(caller, row)

    async def clear_avatar(self, caller: Caller, group_id: str) -> GroupOut:
        row = await self._store.update(
            caller.tenant_id, caller.user_id, group_id, clear_avatar=True
        )
        if row is None:
            raise NotFoundError("Group not found")
        return await self._to_out(caller, row)

    async def pin(self, caller: Caller, group_id: str, pinned: bool) -> GroupOut:
        await self.get(caller, group_id)
        await self._pins.set_pinned(caller, PIN_KIND, group_id, pinned)
        return await self.get(caller, group_id)

    async def add_member(
        self, caller: Caller, group_id: str, agent_id: str, mode: str = "always"
    ) -> GroupOut:
        await self.get(caller, group_id)
        await self._store.add_member(group_id, agent_id, mode)
        return await self.get(caller, group_id)

    async def remove_member(self, caller: Caller, group_id: str, agent_id: str) -> GroupOut:
        await self.get(caller, group_id)
        await self._store.remove_member(group_id, agent_id)
        return await self.get(caller, group_id)

    async def set_member_mode(
        self, caller: Caller, group_id: str, agent_id: str, mode: str
    ) -> GroupOut:
        await self.get(caller, group_id)
        await self._store.set_member_mode(group_id, agent_id, mode)
        return await self.get(caller, group_id)

    async def read_messages(
        self, caller: Caller, group_id: str, before: int | None = None, limit: int = 50
    ) -> MessageList:
        await self.get(caller, group_id)
        ch = self._channel_name(group_id)
        entries = (
            await self._channels.channel_last(ch, limit)
            if before is None
            else await self._channels.channel_read_before(ch, before, limit)
        )
        items = [
            MessageOut(
                seq=e.seq,
                sender_id=str(e.sender.key if e.sender else ""),
                sender_name=str(e.sender.type if e.sender else ""),
                text=e.text or "",
                created_at=e.at,
            )
            for e in entries
        ]
        return MessageList(items=items)

    async def send_message(
        self,
        caller: Caller,
        group_id: str,
        text: str,
        attachments: list[str] | None = None,
    ) -> MessageOut:
        await self.get(caller, group_id)
        ch = self._channel_name(group_id)
        res = await self._channels.channel_append(
            ch,
            sender=Actor("user", caller.user_id),
            text=text,
            kind=EntryKind.MESSAGE,
            data={"attachments": attachments or []},
        )
        return MessageOut(
            seq=res.seq or 0,
            sender_id=caller.user_id,
            sender_name=caller.name or "You",
            text=text,
            created_at=datetime.now(tz=UTC),
        )

    async def delete_message(self, caller: Caller, group_id: str, seq: int) -> None:
        await self.get(caller, group_id)

    async def edit_message(self, caller: Caller, group_id: str, seq: int, text: str) -> MessageOut:
        await self.get(caller, group_id)
        return MessageOut(
            seq=seq,
            sender_id=caller.user_id,
            sender_name=caller.name or "You",
            text=text,
            created_at=datetime.now(tz=UTC),
        )

    async def react(self, caller: Caller, group_id: str, seq: int, reaction: str) -> None:
        await self.get(caller, group_id)

    async def mark_read(self, caller: Caller, group_id: str) -> None:
        await self.get(caller, group_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def groups(container: Container) -> GroupsService:
    return GroupsService(
        container,
        GroupsStore(container.database),
        container.get(pins),
        container.get(avatars),
        container.get(channels),
    )
