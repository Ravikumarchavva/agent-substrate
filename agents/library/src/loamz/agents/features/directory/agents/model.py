from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_AGENTS_PER_USER = 20
MAX_NAME_CHARS = 60


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    role: str = Field(default="", max_length=160)
    instructions: str = Field(default="", max_length=8000)
    allowed_tools: list[str] | None = None
    """`None`: every tool the deployment offers."""
    model: str | None = Field(default=None, max_length=120)
    """`provider/name`; `None`: the deployment's own."""


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_CHARS)
    role: str | None = Field(default=None, max_length=160)
    instructions: str | None = Field(default=None, max_length=8000)
    allowed_tools: list[str] | None = None
    all_tools: bool = False
    """`allowed_tools: null` is "leave as it is", so going back to every tool needs its own flag."""
    model: str | None = Field(default=None, max_length=120)
    default_model: bool = False
    """Likewise `model: null` leaves it as it is, so going back to the deployment's own needs a flag."""


class AgentProfile(BaseModel):
    """An agent's saved definition as plain values: what anything that builds the agent (a chat, a delegation,
    a group member) needs, with no request attached."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    user_id: str
    name: str
    role: str = ""
    instructions: str = ""
    allowed_tools: tuple[str, ...] | None = None
    model: str | None = None
    avatar_key: str | None = None
    created_at: float = 0.0

    @property
    def workspace_id(self) -> str:
        """The id this agent's files are keyed by (the path segment where a conversation's id would be)."""
        return f"dot-{self.id}"


class AgentOut(BaseModel):
    id: str
    name: str
    role: str
    instructions: str
    allowed_tools: list[str] | None = None
    workspace_id: str
    created_at: datetime
    thread_id: str | None = None
    """The agent's one conversation (absent until it is first opened)."""
    last_active: datetime | None = None
    """When that conversation last moved: never the thread's own time, opening a chat is not a message."""
    last_message: str | None = None
    working: bool = False
    avatar: str | None = None
    pinned_at: datetime | None = None
    model: str | None = None
    sees: bool | None = None
    """Whether its own model reads pictures (`None` while it uses the deployment's)."""


class AgentList(BaseModel):
    items: list[AgentOut]
    next_page_token: str | None = None


class ThreadRef(BaseModel):
    id: str


class ToolInfo(BaseModel):
    name: str
    description: str = ""


class ToolList(BaseModel):
    items: list[ToolInfo]


class ContactIn(BaseModel):
    agent_id: str
    note: str = Field(default="", max_length=300)


class ContactsIn(BaseModel):
    contacts: list[ContactIn]


class ContactOut(BaseModel):
    agent_id: str
    name: str
    role: str
    note: str


class ContactList(BaseModel):
    items: list[ContactOut]


@dataclass(frozen=True, slots=True)
class Drive:
    """One group's shared folder as an agent's code sees it."""

    label: str
    """The folder: /groups/<label>."""
    group: str
    """The group's name, as people know it."""
    workspace_id: str
    """`group-<id>`."""


def files_instructions(drives: tuple[Drive, ...] | list[Drive]) -> str:
    """Tell the model where its files are."""
    lines = [
        "Your files: /workspace is your own folder. What you save there stays between all your conversations, "
        "and only you can see it."
    ]
    if drives:
        lines.append(
            "Each group you belong to has a shared folder that everyone in it can read and write: "
            "the same files for all of them."
        )
        lines += [f'  /groups/{d.label} - the group "{d.group}"' for d in drives]
        lines.append(
            "What the user shares in a group is saved in its uploads/ folder. "
            "Use the code tool to read, edit or make files in any of these."
        )
    return "\n".join(lines)
