"""The conversation DAG is the only history model: a linear transcript is a
projection of one branch, and a session can be deleted as a unit."""

from __future__ import annotations

import pytest

from substrate.agents.context import InMemoryHistoryProvider
from substrate.agents.context.history import project_messages
from substrate.kernel.core.content import ChatMessage, TextBlock
from substrate.kernel.storage.history import HistoryProvider, MessageNode


def _msg(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=[TextBlock(text=text)])


async def _append(provider, session_id: str, text: str, *, run_id: str = "", branch: str = "main"):
    b = await provider.get_branch(session_id, branch)
    node = MessageNode(
        parent_id=b.head_message_id if b else None,
        session_id=session_id,
        run_id=run_id,
        payload=_msg(text),
    )
    await provider.append_and_advance(node, branch)
    return node


@pytest.mark.asyncio
async def test_history_provider_contract():
    provider = InMemoryHistoryProvider()
    assert isinstance(provider, HistoryProvider)

    assert await project_messages(provider, "session-abc") == []

    await _append(provider, "session-abc", "hello", run_id="run-1")

    msgs = await project_messages(provider, "session-abc")
    assert [m.content[0].text for m in msgs] == ["hello"]
    assert msgs[0].role == "user"
    assert await project_messages(provider, "session-other") == []


@pytest.mark.asyncio
async def test_branches_project_independently():
    provider = InMemoryHistoryProvider()
    first = await _append(provider, "s", "one")
    await _append(provider, "s", "two")
    await provider.fork_branch("s", "main", "alt", fork_from_message_id=first.id)
    await _append(provider, "s", "alt-two", branch="alt")

    assert [m.content[0].text for m in await project_messages(provider, "s")] == ["one", "two"]
    assert [m.content[0].text for m in await project_messages(provider, "s", branch_id="alt")] == [
        "one",
        "alt-two",
    ]


@pytest.mark.asyncio
async def test_delete_session_removes_only_that_session():
    provider = InMemoryHistoryProvider()
    await _append(provider, "keep", "stay")
    doomed = await _append(provider, "gone", "bye")

    await provider.delete_session("gone")

    assert await project_messages(provider, "gone") == []
    assert await provider.get_node(doomed.id) is None
    assert [m.content[0].text for m in await project_messages(provider, "keep")] == ["stay"]
    await provider.delete_session("never-existed")  # idempotent
