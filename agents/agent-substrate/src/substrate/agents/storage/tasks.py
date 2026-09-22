"""In-memory task store — per-agent Kanban boards, keyed by (conversation_id, agent_id, branch_id).

Constructor-injected everywhere, like every other backend in this codebase —
see ``serving/factory.py::init_infrastructure`` for where a
``PgTaskStore`` is built instead when ``RUNTIME_BACKEND=postgres``.
"""

from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from substrate.kernel.storage.tasks import Task, TaskList, TaskStatus

# ---------------------------------------------------------------------------
# Per-request/agent identity ContextVars (set by agent run() entry).
# Placed here (L1) so both agents/core/* and capabilities/tools/* can import them
# without violating the layer contracts (capabilities may import agents).
#
# Workspace-ownership ContextVars (current_user_id, current_tenant_id,
# current_branch_id) live in agents/workspace/scope.py — a different concern
# (whose files these are), not task-list identity.
# ---------------------------------------------------------------------------

current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_manager_thread_id", default="default"
)
current_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_manager_agent_id", default=""
)
current_agent_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_manager_agent_label", default=""
)
current_parent_agent_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "task_manager_parent_agent_id", default=None
)


class TaskStore:
    """Thread-safe in-memory task store."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # task_list_id -> TaskList
        self._lists: Dict[str, TaskList] = {}
        # (conversation_id, agent_id, branch_id) -> task_list_id
        self._by_key: Dict[tuple[str, str, str], str] = {}

    async def create_task_list(
        self,
        conversation_id: str,
        task_titles: List[str],
        *,
        agent_id: str = "",
        agent_label: str = "",
        parent_agent_id: Optional[str] = None,
        max_retries: int = 3,
        branch_id: str = "main",
    ) -> TaskList:
        async with self._lock:
            task_list = TaskList(
                id=str(uuid4()),
                conversation_id=conversation_id,
                max_retries=max_retries,
                agent_id=agent_id,
                agent_label=agent_label,
                parent_agent_id=parent_agent_id,
                branch_id=branch_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                tasks=[
                    Task(
                        id=str(uuid4()),
                        title=t.strip(),
                        status=TaskStatus.PLANNED,
                        order=i,
                    )
                    for i, t in enumerate(task_titles)
                    if t.strip()
                ],
            )
            self._lists[task_list.id] = task_list
            self._by_key[(conversation_id, agent_id, branch_id)] = task_list.id
            return task_list

    async def get_task_list(self, task_list_id: str) -> Optional[TaskList]:
        return self._lists.get(task_list_id)

    async def get_by_conversation(
        self, conversation_id: str, branch_id: str = "main"
    ) -> Optional[TaskList]:
        # Return the "root" board (agent_id="") or the first one found,
        # within this branch only.
        tl_id = self._by_key.get((conversation_id, "", branch_id))
        if tl_id:
            return self._lists.get(tl_id)
        # Fallback: any board for this conversation on this branch
        for (cid, _, bid), tl_id in self._by_key.items():
            if cid == conversation_id and bid == branch_id:
                return self._lists.get(tl_id)
        return None

    async def get_boards_by_conversation(
        self, conversation_id: str, branch_id: str = "main"
    ) -> List[TaskList]:
        results = []
        for (cid, _, bid), tl_id in self._by_key.items():
            if cid == conversation_id and bid == branch_id:
                tl = self._lists.get(tl_id)
                if tl:
                    results.append(tl)
        return results

    async def settle_conversation(self, conversation_id: str) -> List[TaskList]:
        """Settle a conversation's boards when its run ends.

        Flips any lingering ``in_progress`` task to ``succeeded`` so the UI
        board stops spinning after the agent has produced its final answer.
        Planned/blocked/failed/abandoned tasks are left untouched. Returns the
        boards that changed.
        """
        async with self._lock:
            changed: List[TaskList] = []
            for (cid, _, bid), tl_id in self._by_key.items():
                if cid != conversation_id or bid != "main":
                    continue
                task_list = self._lists.get(tl_id)
                if not task_list:
                    continue
                new_tasks = []
                mutated = False
                for task in task_list.tasks:
                    if task.status == TaskStatus.IN_PROGRESS:
                        new_tasks.append(
                            task.model_copy(update={"status": TaskStatus.SUCCEEDED})
                        )
                        mutated = True
                    else:
                        new_tasks.append(task)
                if mutated:
                    updated = task_list.model_copy(update={"tasks": new_tasks})
                    self._lists[tl_id] = updated
                    changed.append(updated)
            return changed

    async def update_status(
        self, task_list_id: str, task_id: str, status: TaskStatus, note: str = ""
    ) -> Optional[Task]:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return None
            for task in task_list.tasks:
                if task.id == task_id:
                    new_task = task.model_copy(
                        update={"status": status, "note": note if note else task.note}
                    )
                    self._lists[task_list_id] = task_list.model_copy(
                        update={
                            "tasks": [
                                new_task if t.id == task_id else t for t in task_list.tasks
                            ]
                        }
                    )
                    return new_task
            return None

    async def add_tasks(self, task_list_id: str, titles: List[str]) -> List[Task]:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return []
            start_order = len(task_list.tasks)
            new_tasks = [
                Task(
                    id=str(uuid4()),
                    title=t.strip(),
                    status=TaskStatus.PLANNED,
                    order=start_order + i,
                )
                for i, t in enumerate(titles)
                if t.strip()
            ]
            self._lists[task_list_id] = task_list.model_copy(
                update={"tasks": [*task_list.tasks, *new_tasks]}
            )
            return new_tasks

    async def delete_task(self, task_list_id: str, task_id: str) -> bool:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return False
            before = len(task_list.tasks)
            new_tasks = [t for t in task_list.tasks if t.id != task_id]
            self._lists[task_list_id] = task_list.model_copy(update={"tasks": new_tasks})
            return len(new_tasks) < before

    async def increment_retry(self, task_list_id: str, task_id: str) -> Optional[Task]:
        """Agent bounded retry: increment retry_count, move to in_progress.

        Returns None if not found or retry_count has reached max_retries.
        """
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return None
            for task in task_list.tasks:
                if task.id == task_id:
                    if task.retry_count >= task_list.max_retries:
                        return None
                    new_task = task.model_copy(
                        update={
                            "retry_count": task.retry_count + 1,
                            "status": TaskStatus.IN_PROGRESS,
                        }
                    )
                    self._lists[task_list_id] = task_list.model_copy(
                        update={
                            "tasks": [
                                new_task if t.id == task_id else t for t in task_list.tasks
                            ]
                        }
                    )
                    return new_task
            return None

    async def force_retry(self, task_list_id: str, task_id: str) -> Optional[Task]:
        """User override: reset retry_count to 0 and set in_progress."""
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return None
            for task in task_list.tasks:
                if task.id == task_id:
                    new_task = task.model_copy(
                        update={
                            "retry_count": 0,
                            "status": TaskStatus.IN_PROGRESS,
                            "note": "",
                        }
                    )
                    self._lists[task_list_id] = task_list.model_copy(
                        update={
                            "tasks": [
                                new_task if t.id == task_id else t for t in task_list.tasks
                            ]
                        }
                    )
                    return new_task
            return None

    async def update_task_title(
        self, task_list_id: str, task_id: str, title: str
    ) -> Optional[Task]:
        async with self._lock:
            task_list = self._lists.get(task_list_id)
            if not task_list:
                return None
            for task in task_list.tasks:
                if task.id == task_id:
                    new_task = task.model_copy(update={"title": title.strip()})
                    self._lists[task_list_id] = task_list.model_copy(
                        update={
                            "tasks": [
                                new_task if t.id == task_id else t for t in task_list.tasks
                            ]
                        }
                    )
                    return new_task
            return None
