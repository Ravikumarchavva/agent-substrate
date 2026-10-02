"""Tests for TaskStore.settle_conversation — board reconciliation on run end."""

from __future__ import annotations

from tests._stores import fs_tasks

from substrate.stores import TaskStatus


async def test_settle_flips_in_progress_to_succeeded() -> None:
    store = fs_tasks()
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
    store = fs_tasks()
    tl = await store.create_task_list("conv-2", ["x", "y"], agent_id="root")
    await store.update_status(tl.id, tl.tasks[0].id, TaskStatus.FAILED)
    await store.update_status(tl.id, tl.tasks[1].id, TaskStatus.BLOCKED)

    changed = await store.settle_conversation("conv-2")

    assert changed == []  # nothing was in_progress
    settled = await store.get_task_list(tl.id)
    statuses = {t.title: t.status for t in settled.tasks}
    assert statuses == {"x": TaskStatus.FAILED, "y": TaskStatus.BLOCKED}


async def test_settle_is_scoped_to_conversation() -> None:
    store = fs_tasks()
    tl_a = await store.create_task_list("conv-a", ["one"], agent_id="root")
    tl_b = await store.create_task_list("conv-b", ["two"], agent_id="root")
    await store.update_status(tl_a.id, tl_a.tasks[0].id, TaskStatus.IN_PROGRESS)
    await store.update_status(tl_b.id, tl_b.tasks[0].id, TaskStatus.IN_PROGRESS)

    await store.settle_conversation("conv-a")

    a = await store.get_task_list(tl_a.id)
    b = await store.get_task_list(tl_b.id)
    assert a.tasks[0].status == TaskStatus.SUCCEEDED
    assert b.tasks[0].status == TaskStatus.IN_PROGRESS  # other conversation untouched


async def test_workers_racing_for_the_last_retry_never_both_get_it(tmp_path) -> None:
    """The retry bound is part of the UPDATE, not a check before it. Two stores — two workers on one folder — retry the
    same failing task with one attempt left, 20 times each at once: exactly the attempts that were left succeed."""
    import asyncio

    from substrate.stores import Store

    first, second = Store.at(tmp_path / "s"), Store.at(tmp_path / "s")
    board = await first.tasks.create_task_list("c", ["flaky"], max_retries=3)
    task_id = board.tasks[0].id

    results = await asyncio.gather(*(s.tasks.increment_retry(board.id, task_id) for s in (first, second) for _ in range(20)))

    assert sum(r is not None for r in results) == 3
    assert (await first.tasks.get_task_list(board.id)).tasks[0].retry_count == 3
    await first.aclose()
    await second.aclose()
