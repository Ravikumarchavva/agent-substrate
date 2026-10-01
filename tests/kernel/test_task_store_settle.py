"""Tests for TaskStore.settle_conversation — board reconciliation on run end."""

from __future__ import annotations

from substrate.agents.storage.tasks import TaskStore
from substrate.kernel.storage.tasks import TaskStatus


async def test_settle_flips_in_progress_to_succeeded() -> None:
    store = TaskStore()
    tl = await store.create_task_list("conv-1", ["a", "b", "c"], agent_id="root")
    # Advance: a -> succeeded, b -> in_progress, c stays planned.
    await store.update_status(tl.id, tl.tasks[0].id, TaskStatus.SUCCEEDED)
    await store.update_status(tl.id, tl.tasks[1].id, TaskStatus.IN_PROGRESS)

    changed = await store.settle_conversation("conv-1")

    assert len(changed) == 1
    settled = await store.get_task_list(tl.id)
    statuses = {t.title: t.status for t in settled.tasks}
    # in_progress was flipped; succeeded and planned are untouched.
    assert statuses == {
        "a": TaskStatus.SUCCEEDED,
        "b": TaskStatus.SUCCEEDED,
        "c": TaskStatus.PLANNED,
    }


async def test_settle_leaves_failed_and_blocked_untouched() -> None:
    store = TaskStore()
    tl = await store.create_task_list("conv-2", ["x", "y"], agent_id="root")
    await store.update_status(tl.id, tl.tasks[0].id, TaskStatus.FAILED)
    await store.update_status(tl.id, tl.tasks[1].id, TaskStatus.BLOCKED)

    changed = await store.settle_conversation("conv-2")

    assert changed == []  # nothing was in_progress
    settled = await store.get_task_list(tl.id)
    statuses = {t.title: t.status for t in settled.tasks}
    assert statuses == {"x": TaskStatus.FAILED, "y": TaskStatus.BLOCKED}


async def test_settle_is_scoped_to_conversation() -> None:
    store = TaskStore()
    tl_a = await store.create_task_list("conv-a", ["one"], agent_id="root")
    tl_b = await store.create_task_list("conv-b", ["two"], agent_id="root")
    await store.update_status(tl_a.id, tl_a.tasks[0].id, TaskStatus.IN_PROGRESS)
    await store.update_status(tl_b.id, tl_b.tasks[0].id, TaskStatus.IN_PROGRESS)

    await store.settle_conversation("conv-a")

    a = await store.get_task_list(tl_a.id)
    b = await store.get_task_list(tl_b.id)
    assert a.tasks[0].status == TaskStatus.SUCCEEDED
    assert b.tasks[0].status == TaskStatus.IN_PROGRESS  # other conversation untouched
