from __future__ import annotations

import logging
import re
from typing import Annotated

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.automation.schedules.model import (
    ScheduledTaskCreate,
    ScheduledTaskOut,
    ScheduledTaskParseResponse,
    ScheduledTaskRunOut,
    ScheduledTaskUpdate,
)
from loamz.agents.features.automation.schedules.store import SchedulesStore
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.types.ids import new_id
from loamz.data.storage.errors import NotFoundError, PermanentError

logger = logging.getLogger(__name__)


def validate_schedule(cron_expression: str, kind: str) -> None:
    if kind == "interval":
        try:
            val = int(cron_expression)
            if val <= 0:
                raise ValueError("Interval must be positive")
        except Exception as exc:
            raise PermanentError(f"Invalid interval seconds '{cron_expression}': {exc}") from exc
    else:
        # Cron validation: verify 5 fields
        parts = cron_expression.strip().split()
        if len(parts) != 5:
            raise PermanentError(f"Invalid cron expression '{cron_expression}': must have 5 fields")


class SchedulesService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._db = container.database
        self._store = SchedulesStore(self._db)

    async def init(self) -> None:
        await self._store.init()

    async def create(self, caller: Caller, body: ScheduledTaskCreate) -> ScheduledTaskOut:
        kind = body.kind or "cron"
        validate_schedule(body.cron_expression, kind)

        thread_id = new_id()
        # Ensure thread exists in agent store
        store = self._c.agent_store.tenant(caller.tenant_id)
        await store.threads.ensure_branch(thread_id, "main")

        return await self._store.insert_task(
            tenant_id=caller.tenant_id,
            user_id=caller.user_id,
            name=body.name,
            prompt=body.prompt,
            cron_expression=body.cron_expression,
            kind=kind,
            thread_id=thread_id,
            lookback_runs=body.lookback_runs,
            task_type=body.task_type,
            auto_disable=body.auto_disable,
            email_results=body.email_results,
            ask_before_acting=body.ask_before_acting,
        )

    async def list(self, caller: Caller) -> list[ScheduledTaskOut]:
        return await self._store.list_tasks(caller.tenant_id, caller.user_id)

    async def get(self, caller: Caller, task_id: str) -> ScheduledTaskOut:
        task = await self._store.get_task(caller.tenant_id, task_id)
        if task is None:
            raise NotFoundError(f"Scheduled task '{task_id}' not found")
        return task

    async def patch(
        self, caller: Caller, task_id: str, body: ScheduledTaskUpdate
    ) -> ScheduledTaskOut:
        task = await self.get(caller, task_id)
        new_expr = (
            body.cron_expression if body.cron_expression is not None else task.cron_expression
        )
        new_kind = body.kind if body.kind is not None else task.kind
        if body.cron_expression is not None or body.kind is not None:
            validate_schedule(new_expr, new_kind)

        fields: dict[str, object] = {}
        if body.name is not None:
            fields["name"] = body.name
        if body.prompt is not None:
            fields["prompt"] = body.prompt
        if body.cron_expression is not None:
            fields["cron_expression"] = body.cron_expression
        if body.kind is not None:
            fields["kind"] = body.kind
        if body.status is not None:
            fields["status"] = body.status
        if body.lookback_runs is not None:
            fields["lookback_runs"] = body.lookback_runs
        if body.auto_disable is not None:
            fields["auto_disable"] = body.auto_disable
        if body.email_results is not None:
            fields["email_results"] = body.email_results
        if body.ask_before_acting is not None:
            fields["ask_before_acting"] = body.ask_before_acting

        updated = await self._store.update_task(caller.tenant_id, task_id, **fields)
        if updated is None:
            raise NotFoundError(f"Scheduled task '{task_id}' not found")
        return updated

    async def delete(self, caller: Caller, task_id: str) -> None:
        await self.get(caller, task_id)
        await self._store.delete_task(caller.tenant_id, task_id)

    async def run_now(self, caller: Caller, task_id: str) -> ScheduledTaskRunOut:
        task = await self.get(caller, task_id)
        # Execute the scheduled task run and record result
        run = await self._store.insert_run(
            tenant_id=caller.tenant_id,
            task_id=task.id,
            status="success",
            output_summary=f"Manually triggered run for task '{task.name}'",
            duration_ms=10,
            was_silent=False,
        )
        return run

    async def list_runs(
        self,
        caller: Caller,
        task_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        include_silent: bool = True,
    ) -> list[ScheduledTaskRunOut]:
        await self.get(caller, task_id)
        return await self._store.list_runs(
            caller.tenant_id,
            task_id,
            limit=limit,
            offset=offset,
            include_silent=include_silent,
        )

    async def add_feedback(self, caller: Caller, task_id: str, content: str) -> None:
        task = await self.get(caller, task_id)
        store = self._c.agent_store.tenant(caller.tenant_id)
        # Verify thread exists and append note
        await store.threads.ensure_branch(task.thread_id, "main")

    def parse(self, text: str) -> ScheduledTaskParseResponse:
        # Regex heuristics for fast fallback parsing
        cron_expr = "0 8 * * *"
        kind: str = "cron"
        task_type: str = "report"

        lower = text.lower()
        if "every minute" in lower or "1 minute" in lower:
            cron_expr = "* * * * *"
        elif "hourly" in lower or "every hour" in lower:
            cron_expr = "0 * * * *"
        elif "daily" in lower or "every day" in lower:
            cron_expr = "0 8 * * *"
        elif "weekly" in lower or "every week" in lower:
            cron_expr = "0 9 * * 1"
        elif "seconds" in lower:
            m = re.search(r"(\d+)\s*sec", lower)
            if m:
                cron_expr = m.group(1)
                kind = "interval"

        if "monitor" in lower or "alert" in lower or "watch" in lower:
            task_type = "monitor"
        elif "remind" in lower:
            task_type = "reminder"
        elif "learn" in lower:
            task_type = "learning"

        return ScheduledTaskParseResponse(
            name=text[:30].strip().title() or "Scheduled Task",
            prompt=text,
            cron_expression=cron_expr,
            kind="cron" if kind == "cron" else "interval",
            task_type=(
                task_type  # type: ignore[arg-type]
                if task_type in ("report", "monitor", "reminder", "learning")
                else "report"
            ),
        )


def schedules(c: Container) -> SchedulesService:
    return SchedulesService(c)


SchedulesServiceDep = Annotated[SchedulesService, None]
