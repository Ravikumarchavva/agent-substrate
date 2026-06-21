"""Tests for TaskManagerTool — one-at-a-time advancement guarantees."""

from __future__ import annotations

from agent_substrate.agents.storage.tasks import (
    TaskStore,
    current_agent_id,
    current_parent_agent_id,
    current_thread_id,
)
from agent_substrate.capabilities.tools.task_manager.tool import TaskManagerTool
from agent_substrate.kernel.storage.tasks import TaskStatus


def _board(result):
    return result.structured_content["task_list"]


async def test_start_task_auto_completes_prior_in_progress() -> None:
    """start_task closes any task left in_progress so the board always advances
    even when the model skips complete_task."""
    current_thread_id.set("conv-1")
    current_agent_id.set("root")
    tool = TaskManagerTool(store=TaskStore())

    await tool.execute(action="create_list", tasks=["one", "two", "three"])
    await tool.execute(action="start_task")  # one -> in_progress

    # Skip complete_task; start the next task directly.
    result = await tool.execute(action="start_task")  # auto-completes one, starts two
    tasks = {t["title"]: t["status"] for t in _board(result)["tasks"]}

    assert tasks["one"] == TaskStatus.SUCCEEDED
    assert tasks["two"] == TaskStatus.IN_PROGRESS
    assert tasks["three"] == TaskStatus.PLANNED


async def test_add_task_skips_existing_titles() -> None:
    """Repeated/confused add_task calls can't pile up phantom duplicate steps."""
    current_thread_id.set("conv-dup")
    current_agent_id.set("root")
    tool = TaskManagerTool(store=TaskStore())

    await tool.execute(action="create_list", tasks=["Research", "Compare", "Recommend"])
    # Model re-adds two titles that already exist (different case / whitespace).
    result = await tool.execute(action="add_task", tasks=["  compare ", "RECOMMEND"])

    titles = [t["title"] for t in _board(result)["tasks"]]
    assert titles == ["Research", "Compare", "Recommend"]  # nothing appended


async def test_create_list_dedupes_input() -> None:
    current_thread_id.set("conv-dup2")
    current_agent_id.set("root")
    tool = TaskManagerTool(store=TaskStore())

    result = await tool.execute(
        action="create_list",
        tasks=["Research", "Compare", "Recommend", "compare", "Recommend"],
    )
    titles = [t["title"] for t in _board(result)["tasks"]]
    assert titles == ["Research", "Compare", "Recommend"]


async def test_event_sink_fires_for_subagent_boards_only() -> None:
    """Subagent board updates stream via the sink; root boards do not (they
    already flow through the parent run's event-log tail)."""
    calls: list[tuple[str, dict]] = []

    async def sink(conv_id: str, board: dict) -> None:
        calls.append((conv_id, board))

    # Root agent (no parent) — sink must NOT fire.
    current_thread_id.set("conv-root")
    current_agent_id.set("root")
    current_parent_agent_id.set(None)
    root_tool = TaskManagerTool(store=TaskStore(), event_sink=sink)
    await root_tool.execute(action="create_list", tasks=["a"])
    assert calls == []

    # Subagent (parent set) — sink fires with the nested board.
    current_thread_id.set("conv-sub")
    current_agent_id.set("child")
    current_parent_agent_id.set("root")
    sub_tool = TaskManagerTool(store=TaskStore(), event_sink=sink)
    await sub_tool.execute(action="create_list", tasks=["x", "y"])

    assert len(calls) == 1
    conv_id, board = calls[0]
    assert conv_id == "conv-sub"
    assert board["parent_agent_id"] == "root"
    assert [t["title"] for t in board["tasks"]] == ["x", "y"]

    current_parent_agent_id.set(None)  # reset for other tests


async def test_start_task_does_not_touch_failed_or_blocked() -> None:
    current_thread_id.set("conv-2")
    current_agent_id.set("root")
    tool = TaskManagerTool(store=TaskStore())

    await tool.execute(action="create_list", tasks=["a", "b"])
    await tool.execute(action="start_task")  # a -> in_progress
    await tool.execute(action="fail_task", note="nope")  # a -> failed

    result = await tool.execute(action="start_task")  # b -> in_progress
    tasks = {t["title"]: t["status"] for t in _board(result)["tasks"]}

    assert tasks["a"] == TaskStatus.FAILED  # untouched
    assert tasks["b"] == TaskStatus.IN_PROGRESS
