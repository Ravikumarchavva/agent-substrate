from __future__ import annotations

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.ports.tasks import Task, TaskList, TaskStore
from loamz.engine.agents.kernel.types.scope import Scope
from loamz.engine.agents.stores import AgentStore, bind_tasks
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.conversations.tasks.model import (
    AddTasksRequest,
    Boards,
    TaskOut,
    TaskResult,
    TaskUpdateRequest,
)
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads


def _out(task: Task, *, retries: bool = False) -> TaskOut:
    return TaskOut(
        id=task.id,
        title=task.title,
        status=str(task.status),
        note=task.note or "",
        retry_count=task.retry_count if retries else None,
    )


class TasksService:
    """The Kanban boards of a conversation. Boards belong to a thread; every call checks the thread first."""

    def __init__(self, store: AgentStore, threads_: ThreadsService) -> None:
        self._store = store
        self._threads = threads_

    def _tasks(self, caller: Caller) -> TaskStore:
        return bind_tasks(self._store.tasks, Scope(tenant_id=caller.tenant_id))

    async def _board(self, caller: Caller, task_list_id: str) -> TaskList:
        board = await self._tasks(caller).get_task_list(task_list_id)
        if board is None:
            raise NotFoundError("Task list not found")
        await self._threads.require(caller, board.conversation_id)
        return board

    async def boards(self, caller: Caller, conversation_id: str) -> Boards:
        await self._threads.require(caller, conversation_id)
        found = await self._tasks(caller).get_boards_by_conversation(conversation_id)
        return Boards(boards=[b.to_dict() for b in found])

    async def update(
        self, caller: Caller, task_list_id: str, task_id: str, body: TaskUpdateRequest
    ) -> TaskResult:
        await self._board(caller, task_list_id)
        tasks = self._tasks(caller)
        result = None
        if body.status:
            result = await tasks.update_status(
                task_list_id, task_id, body.status, note=body.note or ""
            )
        if body.title:
            result = await tasks.update_task_title(task_list_id, task_id, body.title)
        if result is None:
            raise NotFoundError("Task not found")
        return TaskResult(task=_out(result))

    async def retry(self, caller: Caller, task_list_id: str, task_id: str) -> TaskResult:
        """User override: reset the retry count and reopen a failed or abandoned task."""
        await self._board(caller, task_list_id)
        result = await self._tasks(caller).force_retry(task_list_id, task_id)
        if result is None:
            raise NotFoundError("Task not found")
        return TaskResult(task=_out(result, retries=True))

    async def add(self, caller: Caller, task_list_id: str, body: AddTasksRequest) -> TaskResult:
        await self._board(caller, task_list_id)
        added = await self._tasks(caller).add_tasks(task_list_id, body.tasks)
        return TaskResult(added=len(added))

    async def delete(self, caller: Caller, task_list_id: str, task_id: str) -> TaskResult:
        await self._board(caller, task_list_id)
        if not await self._tasks(caller).delete_task(task_list_id, task_id):
            raise NotFoundError("Task not found")
        return TaskResult()

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Boards are erased with the tenant's agent state (`AgentStore.tenant(...).erase()`), not here."""
        return 0


def tasks(container: Container) -> TasksService:
    return TasksService(container.agent_store, container.get(threads))
