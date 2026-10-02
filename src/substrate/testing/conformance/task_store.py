"""Conformance suite for ``TaskStore`` (per-agent Kanban boards).

Every implementation — in-memory, Postgres, any a consumer writes — runs exactly these tests: a board
is keyed by (conversation, agent, branch) and replaced, not duplicated; retries are bounded; boards of
different conversations and branches never mix. Subclass it and provide the ``store`` fixture.
"""

from __future__ import annotations

import uuid

import pytest

from substrate.kernel.abstractions.storage.tasks import TaskStatus, TaskStore


class TaskStoreConformance:
    @pytest.fixture
    async def store(self) -> TaskStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @pytest.fixture
    def conv(self) -> str:
        """A conversation id no earlier test used: a database outlives a test."""
        return f"conv-{uuid.uuid4().hex}"

    async def test_a_board_is_created_with_ordered_planned_tasks_and_blank_titles_dropped(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["first", "  ", "second"])
        assert [t.title for t in board.tasks] == ["first", "second"]
        assert [t.order for t in board.tasks] == [0, 1]
        assert all(t.status == TaskStatus.PLANNED for t in board.tasks)
        fetched = await store.get_task_list(board.id)
        assert fetched is not None and [t.title for t in fetched.tasks] == ["first", "second"]

    async def test_a_missing_board_is_none(self, store: TaskStore, conv: str) -> None:
        assert await store.get_task_list("no-such-board") is None
        assert await store.get_by_conversation(conv) is None
        assert await store.get_boards_by_conversation(conv) == []

    async def test_creating_again_for_the_same_key_replaces_the_board(self, store: TaskStore, conv: str) -> None:
        await store.create_task_list(conv, ["old"], agent_id="a")
        new = await store.create_task_list(conv, ["new"], agent_id="a")
        boards = await store.get_boards_by_conversation(conv)
        assert [b.id for b in boards] == [new.id]
        assert [t.title for t in boards[0].tasks] == ["new"]

    async def test_each_agent_has_its_own_board_and_the_root_board_is_the_primary(self, store: TaskStore, conv: str) -> None:
        root = await store.create_task_list(conv, ["plan"])
        await store.create_task_list(conv, ["research"], agent_id="researcher", agent_label="Researcher", parent_agent_id="")
        assert (await store.get_by_conversation(conv)).id == root.id
        assert len(await store.get_boards_by_conversation(conv)) == 2

    async def test_branches_have_separate_boards(self, store: TaskStore, conv: str) -> None:
        main = await store.create_task_list(conv, ["main task"])
        exp = await store.create_task_list(conv, ["exp task"], branch_id="exp")
        assert (await store.get_by_conversation(conv)).id == main.id
        assert (await store.get_by_conversation(conv, "exp")).id == exp.id
        assert [b.id for b in await store.get_boards_by_conversation(conv, "exp")] == [exp.id]

    async def test_conversations_never_share_boards(self, store: TaskStore, conv: str) -> None:
        other = f"{conv}-other"
        await store.create_task_list(conv, ["mine"])
        assert await store.get_by_conversation(other) is None

    async def test_update_status_changes_the_task_and_keeps_the_note_unless_given_one(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["t"])
        tid = board.tasks[0].id
        done = await store.update_status(board.id, tid, TaskStatus.IN_PROGRESS, "working")
        assert done.status == TaskStatus.IN_PROGRESS and done.note == "working"
        again = await store.update_status(board.id, tid, TaskStatus.SUCCEEDED)
        assert again.status == TaskStatus.SUCCEEDED and again.note == "working"
        assert (await store.get_task_list(board.id)).tasks[0].status == TaskStatus.SUCCEEDED

    async def test_updating_something_that_does_not_exist_returns_none(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["t"])
        assert await store.update_status("no-such-board", board.tasks[0].id, TaskStatus.FAILED) is None
        assert await store.update_status(board.id, "no-such-task", TaskStatus.FAILED) is None

    async def test_added_tasks_continue_the_order(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["a", "b"])
        added = await store.add_tasks(board.id, ["c", "", "d"])
        assert [t.title for t in added] == ["c", "d"] and [t.order for t in added] == [2, 3]
        assert [t.title for t in (await store.get_task_list(board.id)).tasks] == ["a", "b", "c", "d"]
        assert await store.add_tasks("no-such-board", ["x"]) == []

    async def test_deleting_a_task_reports_whether_it_existed(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["a", "b"])
        assert await store.delete_task(board.id, board.tasks[0].id) is True
        assert await store.delete_task(board.id, board.tasks[0].id) is False
        assert [t.title for t in (await store.get_task_list(board.id)).tasks] == ["b"]

    async def test_retries_are_bounded_by_the_boards_maximum(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["t"], max_retries=2)
        tid = board.tasks[0].id
        first = await store.increment_retry(board.id, tid)
        assert first.retry_count == 1 and first.status == TaskStatus.IN_PROGRESS
        assert (await store.increment_retry(board.id, tid)).retry_count == 2
        assert await store.increment_retry(board.id, tid) is None, "retries must stop at the maximum"
        assert await store.increment_retry(board.id, "no-such-task") is None

    async def test_a_user_can_force_a_retry_past_the_maximum(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["t"], max_retries=1)
        tid = board.tasks[0].id
        await store.increment_retry(board.id, tid)
        await store.update_status(board.id, tid, TaskStatus.ABANDONED)
        forced = await store.force_retry(board.id, tid)
        assert forced.retry_count == 0 and forced.status == TaskStatus.IN_PROGRESS
        assert await store.force_retry(board.id, "no-such-task") is None

    async def test_renaming_a_task_changes_only_its_title(self, store: TaskStore, conv: str) -> None:
        board = await store.create_task_list(conv, ["old"])
        renamed = await store.update_task_title(board.id, board.tasks[0].id, "new")
        assert renamed.title == "new" and renamed.id == board.tasks[0].id
        assert await store.update_task_title(board.id, "no-such-task", "x") is None

    @pytest.mark.parametrize("hostile", ["x' OR '1'='1", "a'; DROP TABLE task_lists; --", "../../etc/passwd", "ünï-çødé"])
    async def test_a_hostile_conversation_or_agent_name_is_inert(self, store: TaskStore, conv: str, hostile: str) -> None:
        victim = await store.create_task_list(conv, ["victim"])
        mine = await store.create_task_list(f"{conv}{hostile}", ["mine"], agent_id=hostile, branch_id=hostile)
        assert [t.title for t in (await store.get_task_list(mine.id)).tasks] == ["mine"]
        assert [b.id for b in await store.get_boards_by_conversation(f"{conv}{hostile}", hostile)] == [mine.id]
        assert (await store.get_by_conversation(conv)).id == victim.id


__all__ = ["TaskStoreConformance"]
