"""An agent's file system: its own folder, and each group it belongs to as a shared one.

An agent's code sees ``/workspace`` (its home: what it saves stays between all its conversations, and only it sees it) and, for every group it is
in, ``/groups/<label>`` (the group's drive: the same folder for every member, where what the user shares in the group is saved under ``uploads/``).
This is what a run is told about both: the metadata that opens them, and the words that tell the model where they are.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.workspace.layout import conversation_prefix
from substrate_cloud.monolith.models import Group, GroupMember

# The most drives a run opens. The code tool refuses more than ``MAX_MOUNTS`` (checked in the platform's tests), so an agent in many groups gets
# the oldest ones rather than none.
MAX_DRIVES = 16
_LABEL_CHARS = 40


@dataclass(frozen=True, slots=True)
class Drive:
    label: str  # the folder: /groups/<label>
    group: str  # the group's name, as people know it
    workspace_id: str  # group-<id>


def drive_label(name: str) -> str:
    """A folder name from a group's name: lower-case ASCII letters and digits joined by dashes, so any name makes one the sandbox accepts."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = (
        re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:_LABEL_CHARS].strip("-")
    )
    return slug if slug and slug != "private" else "group"


async def drives_of(db: AsyncSession, agent_id: uuid.UUID) -> tuple[Drive, ...]:
    """The groups an agent is in, oldest first, each with its folder. Two groups with one name get ``trip`` and ``trip-2``."""
    rows = await db.execute(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.agent_id == agent_id)
        .order_by(Group.created_at, Group.id)
        .limit(MAX_DRIVES)
    )
    taken: set[str] = set()
    drives: list[Drive] = []
    for group in rows.scalars().all():
        base = drive_label(group.name)
        label, n = base, 2
        while label in taken:
            label, n = f"{base}-{n}", n + 1
        taken.add(label)
        drives.append(
            Drive(label=label, group=group.name, workspace_id=group.workspace_id)
        )
    return tuple(drives)


async def delete_workspace_files(
    store: Any, tenant_id: str, user_id: str, workspace_id: str
) -> int:
    """Delete everything a home or a drive holds in storage (its files, previews, picture): they go with the agent or the group."""
    return await store.delete_prefix(
        conversation_prefix(tenant_id, user_id, workspace_id) + "/"
    )


def run_metadata(home: str, drives: tuple[Drive, ...]) -> dict[str, Any]:
    """What goes on a run's message so its code opens ``home`` as ``/workspace`` and each drive at ``/groups/<label>``."""
    return {
        "workspace_id": home,
        **(
            {"workspace_mounts": {d.label: d.workspace_id for d in drives}}
            if drives
            else {}
        ),
    }


def files_instructions(drives: tuple[Drive, ...]) -> str:
    """Tell the model where its files are."""
    lines = [
        "Your files: /workspace is your own folder. What you save there stays between all your conversations, and only you can see it."
    ]
    if drives:
        lines.append(
            "Each group you belong to has a shared folder that everyone in it can read and write: the same files for all of them."
        )
        lines += [f'  /groups/{d.label} - the group "{d.group}"' for d in drives]
        lines.append(
            "What the user shares in a group is saved in its uploads/ folder. Use the code tool to read, edit or make files in any of these."
        )
    return "\n".join(lines)


__all__ = [
    "Drive",
    "MAX_DRIVES",
    "delete_workspace_files",
    "drive_label",
    "drives_of",
    "files_instructions",
    "run_metadata",
]
