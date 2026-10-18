"""Publish finished batch jobs: commit written files to Iceberg, then update governed records.

Publications are durable: if the API restarts mid-way, pending ones are picked up again and
commits are idempotent (see TableService.commit_files).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any
from uuid import uuid4

import pyarrow as pa
from starlette.concurrency import run_in_threadpool

from substrate_data.api.services import Services
from substrate_data.catalog.backends import Leader
from substrate_data.catalog.store import now
from substrate_data.core.errors import Problem, public_error

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 1.0
TERMINAL = {"succeeded", "failed", "cancelled"}


def schema_from(encoded: str) -> pa.Schema:
    return pa.ipc.read_schema(pa.py_buffer(base64.b64decode(encoded)))


class Publisher:
    def __init__(self, svc: Services):
        self.svc = svc
        self.wake = asyncio.Event()
        # With several API replicas on one PostgreSQL catalog, exactly one publishes finished jobs.
        self.leader: Leader | None = None
        self.last_schedule = 0.0
        self.schedule_runs = 0  # completed scheduling passes
        self.scheduled = 0  # views started by the latest pass

    # ------------------------------------------------------------------ submission
    async def submit(
        self,
        kind: str,
        operation: str,
        payload: dict[str, Any],
        context: dict[str, Any],
        *,
        max_attempts: int = 2,
        queue_timeout_seconds: int = 3600,
    ) -> str:
        job = await self.svc.controller.request(
            "POST",
            "/jobs",
            json={
                "operation": operation,
                "payload": payload,
                "max_attempts": max_attempts,
                "queue_timeout_seconds": queue_timeout_seconds,
            },
        )
        stamp = now()
        with self.svc.store.connection() as db:
            db.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?,?)",
                (job["id"], kind, json.dumps(context), "pending", None, stamp, stamp),
            )
        self.wake.set()
        return job["id"]

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        self.leader = self.svc.store.leader("publisher")
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), INTERVAL_SECONDS)
            except TimeoutError:
                pass
            self.wake.clear()
            if not await run_in_threadpool(self.leader.acquire):
                continue
            try:
                await self.reconcile()
                await self.schedule()
            except Problem as exc:
                logger.info("Publication waiting for compute: %s", exc.detail)
            except Exception:
                logger.exception("Publication reconciliation failed")

    async def schedule(self) -> None:
        """Scheduled materialized-view refreshes, checked at most once a minute (leader only)."""
        if time.monotonic() - self.last_schedule < 60:
            return
        self.last_schedule = time.monotonic()
        from substrate_data.api.routes.materialized import refresh_due

        try:
            self.scheduled = await refresh_due(self.svc, self)
        except Problem as exc:
            logger.info("Scheduled refresh waiting: %s", exc.detail)
        self.schedule_runs += 1

    async def reconcile(self) -> None:
        with self.svc.store.connection() as db:
            pending = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM publications WHERE state='pending' ORDER BY created_at LIMIT 100"
                )
            ]
        for publication in pending:
            job = await self.svc.controller.request("GET", f"/jobs/{publication['job_id']}")
            if job["state"] not in TERMINAL:
                continue
            context = json.loads(publication["payload"])
            try:
                if job["state"] == "succeeded":
                    await self.publish(publication["kind"], context, job["result"])
                else:
                    await run_in_threadpool(
                        self.fail, publication["kind"], context, job.get("error") or job["state"]
                    )
                state, error = "done", None
            except Exception as exc:
                logger.exception("Publishing %s failed", publication["kind"])
                error = public_error(exc, self.svc.settings)
                await run_in_threadpool(self.fail, publication["kind"], context, error)
                state = "failed"
            with self.svc.store.connection() as db:
                db.execute(
                    "UPDATE publications SET state=?, error=?, updated_at=? WHERE job_id=?",
                    (state, error, now(), publication["job_id"]),
                )

    async def publish(self, kind: str, context: dict[str, Any], result: dict[str, Any]) -> None:
        if kind == "import.infer":
            await self.inferred(context, result)
        elif kind == "import.ingest":
            await run_in_threadpool(self.ingested, context, result)
        elif kind == "pipeline.transform":
            await run_in_threadpool(self.transformed, context, result)
        elif kind == "table.optimize":
            await run_in_threadpool(self.optimized, context, result)
        elif kind == "materialized.refresh":
            await run_in_threadpool(self.refreshed, context, result)
        elif kind == "table.expire":
            await run_in_threadpool(self.expired, context, result)

    # ------------------------------------------------------------------ handlers
    async def inferred(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        from substrate_data.api.routes.catalog import create_managed_table

        store = self.svc.store
        record = store.get("imports", context["import_id"], ("target",))
        target = record["target"]
        schema = store.schema(target["catalog"], target["schema"])
        table = await run_in_threadpool(
            create_managed_table,
            self.svc,
            schema,
            target["table"],
            record["principal"],
            None,
            schema_from(result["schema"]),
            target.get("partitioning", []),
            None,
        )
        created = store.table(target["catalog"], target["schema"], target["table"])
        store.update("imports", record["id"], {"table_id": created["id"], "status": "ingesting"})
        await self.ingest(record, created, created_table=True)
        logger.info("Created %s for import %s", table["full_name"], record["id"])

    async def ingest(
        self, record: dict[str, Any], table: dict[str, Any], created_table: bool
    ) -> None:
        iceberg = await run_in_threadpool(self.svc.tables.load, table, True)
        write_id = uuid4().hex
        target = record["target"]
        job_id = await self.submit(
            "import.ingest",
            "ingest",
            {
                "sources": [self.svc.staging.uri(f["key"]) for f in target["files"]],
                "format": record["format"],
                "location": iceberg.location(),
                "write_id": write_id,
                "partitioning": [
                    {"column": p["column"], "transform": p["transform"]}
                    for p in table["partitioning"]
                ],
            },
            {
                "import_id": record["id"],
                "table_id": table["id"],
                "mode": record["mode"],
                "write_id": write_id,
                "created_table": created_table,
            },
        )
        self.svc.store.update("imports", record["id"], {"job_id": job_id, "status": "ingesting"})

    def ingested(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        store = self.svc.store
        table = store.table_by_id(context["table_id"])
        committed = self.svc.tables.commit_files(
            table,
            [f["path"] for f in result["files"]],
            "overwrite" if context["mode"] == "overwrite" else "append",
            context["write_id"],
            schema_from(result["schema"]),
        )
        self.refresh(table, committed)
        record = store.get("imports", context["import_id"], ("target",))
        store.update("imports", record["id"], {"status": "succeeded", "rows": result["rows"]})
        store.audit(
            None,
            "import.complete",
            ".".join((table["catalog_name"], table["schema_name"], table["name"])),
            "success",
            import_id=record["id"],
            rows=result["rows"],
            requested_by=record["principal"],
        )
        self.svc.staging.remove([f["key"] for f in record["target"]["files"]])

    def transformed(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        store = self.svc.store
        table = store.table_by_id(context["table_id"])
        committed = self.svc.tables.commit_files(
            table, [f["path"] for f in result["files"]], "overwrite", context["write_id"]
        )
        self.refresh(table, committed)
        store.update(
            "pipeline_runs",
            context["run_id"],
            {
                "status": "succeeded",
                "rows": result["rows"],
                "output_table_id": table["id"],
                "finished_at": now(),
            },
        )

    def optimized(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        store = self.svc.store
        table = store.table_by_id(context["table_id"])
        committed = self.svc.tables.replace_files(
            table, context["old_files"], [f["path"] for f in result["files"]], context["write_id"]
        )
        self.refresh(table, committed)
        store.update(
            "maintenance_runs",
            context["run_id"],
            {
                "status": "succeeded",
                "files_out": len(result["files"]),
                "rows": result["rows"],
                "bytes_after": result["bytes"],
                "finished_at": now(),
            },
        )
        store.audit(
            None,
            "table.optimize",
            ".".join((table["catalog_name"], table["schema_name"], table["name"])),
            "success",
            run_id=context["run_id"],
            files_in=len(context["old_files"]),
            files_out=len(result["files"]),
            requested_by=context["principal"],
        )

    def expired(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        self.svc.store.update(
            "maintenance_runs",
            context["run_id"],
            {"status": "succeeded", "files_out": result["removed"], "finished_at": now()},
        )

    def refreshed(self, context: dict[str, Any], result: dict[str, Any]) -> None:
        store = self.svc.store
        table = store.table_by_id(context["table_id"])
        committed = self.svc.tables.commit_files(
            table,
            [f["path"] for f in result["files"]],
            context.get("mode", "overwrite"),
            context["write_id"],
        )
        self.refresh(table, committed)
        definition = {
            **table["materialized"],
            "status": "fresh",
            "refreshed_at": now(),
            "sources": context["sources"],
            "error": None,
        }
        store.update_table_state(table["id"], {"materialized": definition})
        store.update(
            "maintenance_runs",
            context["run_id"],
            {
                "status": "succeeded",
                "files_out": len(result["files"]),
                "rows": result["rows"],
                "bytes_after": sum(f["size"] for f in result["files"]),
                "finished_at": now(),
            },
        )
        store.audit(
            None,
            "materialized_view.refresh",
            ".".join((table["catalog_name"], table["schema_name"], table["name"])),
            "success",
            run_id=context["run_id"],
            rows=result["rows"],
            requested_by=context["principal"],
        )

    def refresh(self, table: dict[str, Any], committed: Any) -> None:
        described = self.svc.tables.describe(committed)
        self.svc.store.update_table_state(
            table["id"],
            {
                "snapshot_id": described["snapshot_id"],
                "row_count": described["row_count"],
                "size_bytes": described["size_bytes"],
                "file_count": described["file_count"],
                "columns": described["columns"],
                "partitioning": described["partitioning"],
            },
        )

    def fail(self, kind: str, context: dict[str, Any], error: str) -> None:
        store = self.svc.store
        if kind.startswith("import."):
            store.update(
                "imports", context["import_id"], {"status": "failed", "error": error[:1000]}
            )
            if context.get("created_table") and context.get("table_id"):
                # A failed create-import leaves no empty table behind.
                try:
                    table = store.table_by_id(context["table_id"])
                    store.delete_securable("table", table, None)
                    self.svc.tables.drop(table)
                except Problem:
                    pass
        elif kind in {"table.optimize", "materialized.refresh", "table.expire"}:
            store.update(
                "maintenance_runs",
                context["run_id"],
                {"status": "failed", "error": error[:1000], "finished_at": now()},
            )
            if kind == "materialized.refresh":
                try:
                    table = store.table_by_id(context["table_id"])
                    failed = {**table["materialized"], "status": "failed", "error": error[:500]}
                    store.update_table_state(table["id"], {"materialized": failed})
                except Problem:
                    pass  # the view was dropped while it refreshed
        elif kind == "pipeline.transform":
            store.update(
                "pipeline_runs",
                context["run_id"],
                {"status": "failed", "error": error[:1000], "finished_at": now()},
            )
