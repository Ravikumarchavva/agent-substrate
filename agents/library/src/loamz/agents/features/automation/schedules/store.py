from __future__ import annotations

import time

from loamz.agents.features.automation.schedules.model import ScheduledTaskOut, ScheduledTaskRunOut
from loamz.agents.kernel.types.ids import new_id
from loamz.data.storage.sql import Database, Row

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        cron_expression TEXT NOT NULL,
        kind TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        status TEXT NOT NULL,
        lookback_runs INTEGER NOT NULL,
        task_type TEXT NOT NULL,
        auto_disable INTEGER NOT NULL,
        email_results INTEGER NOT NULL,
        ask_before_acting INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        last_run_at REAL,
        next_run_at REAL
    );
    CREATE INDEX IF NOT EXISTS scheduled_tasks_tenant_idx ON scheduled_tasks (tenant_id, user_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduled_task_runs (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        status TEXT NOT NULL,
        output_summary TEXT NOT NULL,
        executed_at REAL NOT NULL,
        duration_ms INTEGER NOT NULL,
        was_silent INTEGER NOT NULL,
        error_message TEXT,
        tokens INTEGER NOT NULL,
        cost_usd REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS scheduled_task_runs_task_idx ON scheduled_task_runs (task_id, executed_at DESC);
    """,
]


class SchedulesStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._migrated = False

    async def _init(self) -> None:
        if not self._migrated:
            async with self._db.transaction() as tx:
                for ddl in SCHEMA:
                    for stmt in filter(None, (s.strip() for s in ddl.split(";"))):
                        await tx.execute(stmt)
            self._migrated = True

    async def insert_task(
        self,
        tenant_id: str,
        user_id: str,
        name: str,
        prompt: str,
        cron_expression: str,
        kind: str,
        thread_id: str,
        status: str = "active",
        lookback_runs: int = 5,
        task_type: str = "report",
        auto_disable: bool = False,
        email_results: bool = False,
        ask_before_acting: bool = True,
        next_run_at: float | None = None,
    ) -> ScheduledTaskOut:
        await self._init()
        now = time.time()
        task_id = new_id()
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                INSERT INTO scheduled_tasks (
                    id, tenant_id, user_id, name, prompt, cron_expression, kind, thread_id,
                    status, lookback_runs, task_type, auto_disable, email_results, ask_before_acting,
                    created_at, updated_at, last_run_at, next_run_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                task_id,
                tenant_id,
                user_id,
                name,
                prompt,
                cron_expression,
                kind,
                thread_id,
                status,
                lookback_runs,
                task_type,
                1 if auto_disable else 0,
                1 if email_results else 0,
                1 if ask_before_acting else 0,
                now,
                now,
                next_run_at,
            )
        return ScheduledTaskOut(
            id=task_id,
            user_id=user_id,
            name=name,
            prompt=prompt,
            cron_expression=cron_expression,
            kind=kind,
            thread_id=thread_id,
            status=status,
            lookback_runs=lookback_runs,
            task_type=task_type,
            auto_disable=auto_disable,
            email_results=email_results,
            ask_before_acting=ask_before_acting,
            created_at=now,
            updated_at=now,
            next_run_at=next_run_at,
            recent_runs=[],
        )

    async def get_task(self, tenant_id: str, task_id: str) -> ScheduledTaskOut | None:
        await self._init()
        async with self._db.transaction() as tx:
            r = await tx.fetchone(
                "SELECT * FROM scheduled_tasks WHERE tenant_id = ? AND id = ?",
                tenant_id,
                task_id,
            )
        if not r:
            return None
        recent = await self.recent_runs(tenant_id, task_id, limit=5)
        return self._task_from_row(r, recent)

    async def list_tasks(self, tenant_id: str, user_id: str) -> list[ScheduledTaskOut]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT * FROM scheduled_tasks WHERE tenant_id = ? AND user_id = ? ORDER BY created_at DESC",
                tenant_id,
                user_id,
            )
        res: list[ScheduledTaskOut] = []
        for r in rows:
            recent = await self.recent_runs(tenant_id, str(r["id"]), limit=5)
            res.append(self._task_from_row(r, recent))
        return res

    async def update_task(
        self,
        tenant_id: str,
        task_id: str,
        **fields: object,
    ) -> ScheduledTaskOut | None:
        await self._init()
        if not fields:
            return await self.get_task(tenant_id, task_id)

        fields["updated_at"] = time.time()
        clauses: list[str] = []
        values: list[object] = []
        for k, v in fields.items():
            clauses.append(f"{k} = ?")
            if isinstance(v, bool):
                values.append(1 if v else 0)
            else:
                values.append(v)
        values.extend([tenant_id, task_id])
        query = f"UPDATE scheduled_tasks SET {', '.join(clauses)} WHERE tenant_id = ? AND id = ?"
        async with self._db.transaction() as tx:
            await tx.execute(query, *values)
        return await self.get_task(tenant_id, task_id)

    async def delete_task(self, tenant_id: str, task_id: str) -> bool:
        await self._init()
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM scheduled_task_runs WHERE tenant_id = ? AND task_id = ?",
                tenant_id,
                task_id,
            )
            await tx.execute(
                "DELETE FROM scheduled_tasks WHERE tenant_id = ? AND id = ?",
                tenant_id,
                task_id,
            )
        return True

    async def insert_run(
        self,
        tenant_id: str,
        task_id: str,
        status: str,
        output_summary: str = "",
        duration_ms: int = 0,
        was_silent: bool = False,
        error_message: str | None = None,
        tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> ScheduledTaskRunOut:
        await self._init()
        run_id = new_id()
        now = time.time()
        async with self._db.transaction() as tx:
            await tx.execute(
                """
                INSERT INTO scheduled_task_runs (
                    id, task_id, tenant_id, status, output_summary, executed_at,
                    duration_ms, was_silent, error_message, tokens, cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                run_id,
                task_id,
                tenant_id,
                status,
                output_summary,
                now,
                duration_ms,
                1 if was_silent else 0,
                error_message,
                tokens,
                cost_usd,
            )
            await tx.execute(
                "UPDATE scheduled_tasks SET last_run_at = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
                now,
                now,
                tenant_id,
                task_id,
            )
        return ScheduledTaskRunOut(
            id=run_id,
            task_id=task_id,
            status=status,
            output_summary=output_summary,
            executed_at=now,
            duration_ms=duration_ms,
            was_silent=was_silent,
            error_message=error_message,
            tokens=tokens,
            cost_usd=cost_usd,
        )

    async def recent_runs(
        self, tenant_id: str, task_id: str, limit: int = 5
    ) -> list[ScheduledTaskRunOut]:
        await self._init()
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT * FROM scheduled_task_runs WHERE tenant_id = ? AND task_id = ? ORDER BY executed_at DESC LIMIT ?",
                tenant_id,
                task_id,
                limit,
            )
        return [self._run_from_row(r) for r in rows]

    async def list_runs(
        self,
        tenant_id: str,
        task_id: str,
        limit: int = 20,
        offset: int = 0,
        include_silent: bool = True,
    ) -> list[ScheduledTaskRunOut]:
        await self._init()
        async with self._db.transaction() as tx:
            if include_silent:
                rows = await tx.fetchall(
                    "SELECT * FROM scheduled_task_runs WHERE tenant_id = ? AND task_id = ? ORDER BY executed_at DESC LIMIT ? OFFSET ?",
                    tenant_id,
                    task_id,
                    limit,
                    offset,
                )
            else:
                rows = await tx.fetchall(
                    "SELECT * FROM scheduled_task_runs WHERE tenant_id = ? AND task_id = ? AND was_silent = 0 ORDER BY executed_at DESC LIMIT ? OFFSET ?",
                    tenant_id,
                    task_id,
                    limit,
                    offset,
                )
        return [self._run_from_row(r) for r in rows]

    def _task_from_row(self, r: Row, recent: list[ScheduledTaskRunOut]) -> ScheduledTaskOut:
        return ScheduledTaskOut(
            id=str(r["id"]),
            user_id=str(r["user_id"]) if r["user_id"] is not None else None,
            name=str(r["name"]),
            prompt=str(r["prompt"]),
            cron_expression=str(r["cron_expression"]),
            kind=str(r["kind"]),
            thread_id=str(r["thread_id"]),
            status=str(r["status"]),
            lookback_runs=int(r["lookback_runs"]),  # type: ignore[arg-type]
            task_type=str(r["task_type"]),
            auto_disable=bool(r["auto_disable"]),
            email_results=bool(r["email_results"]),
            ask_before_acting=bool(r["ask_before_acting"]),
            created_at=float(r["created_at"]),  # type: ignore[arg-type]
            updated_at=float(r["updated_at"]),  # type: ignore[arg-type]
            last_run_at=float(r["last_run_at"]) if r["last_run_at"] is not None else None,  # type: ignore[arg-type]
            next_run_at=float(r["next_run_at"]) if r["next_run_at"] is not None else None,  # type: ignore[arg-type]
            recent_runs=recent,
        )

    def _run_from_row(self, r: Row) -> ScheduledTaskRunOut:
        return ScheduledTaskRunOut(
            id=str(r["id"]),
            task_id=str(r["task_id"]),
            status=str(r["status"]),
            output_summary=str(r["output_summary"] or ""),
            executed_at=float(r["executed_at"]),  # type: ignore[arg-type]
            duration_ms=int(r["duration_ms"]),  # type: ignore[arg-type]
            was_silent=bool(r["was_silent"]),
            error_message=str(r["error_message"]) if r["error_message"] is not None else None,
            tokens=int(r["tokens"]),  # type: ignore[arg-type]
            cost_usd=float(r["cost_usd"]),  # type: ignore[arg-type]
        )
