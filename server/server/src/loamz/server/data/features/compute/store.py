"""Transactional compute state. The worker data plane never opens this database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from loamz.server.data.features.compute.models import JobRequest, ResourceSpec
from loamz.server.data.kernel.metadata_backends import PostgresBackend

SCHEMA = """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS config(id TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS attachments(resource_id TEXT PRIMARY KEY, body TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
        state TEXT NOT NULL, created REAL NOT NULL, available REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
        fingerprint TEXT NOT NULL, idempotency_key TEXT UNIQUE, body TEXT NOT NULL,
        result TEXT, error TEXT, instance_id TEXT, started REAL, finished REAL);
    CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(state, available, created);
    CREATE TABLE IF NOT EXISTS attempts(job_id TEXT, number INTEGER, body TEXT,
        PRIMARY KEY(job_id, number));
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
        resource_id TEXT, job_id TEXT, at REAL, message TEXT);
"""


class ComputeStore:
    """SQLite (one controller) or PostgreSQL (`url`: several controllers, one of them active)."""

    def __init__(self, path: Path | None = None, *, url: str = ""):
        self.path = path
        self.backend = PostgresBackend(url, SCHEMA, (), pool_size=8) if url else None
        if self.backend is None:
            assert path is not None
            path.parent.mkdir(parents=True, exist_ok=True)
            with self.connection() as db:
                db.executescript(SCHEMA)
            path.chmod(0o600)

    @property
    def shared(self) -> bool:
        return self.backend is not None

    def leader(self, name: str):
        return self.backend.leader(name) if self.backend else None

    def close(self) -> None:
        if self.backend:
            self.backend.close()

    @contextmanager
    def connection(self):
        if self.backend:
            with self.backend.connection() as db:
                yield db
            return
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def bind_storage(self, identity):
        with self.connection() as db:
            db.execute(
                "INSERT INTO config VALUES('storage',?) ON CONFLICT (id) DO NOTHING", (identity,)
            )
            if (
                db.execute("SELECT value FROM config WHERE id='storage'").fetchone()["value"]
                != identity
            ):
                raise ValueError(
                    "Compute state is bound to another storage location; configure a separate compute state directory"
                )

    def event(self, resource_id, message, job_id=None):
        with self.connection() as db:
            db.execute(
                "INSERT INTO events(resource_id,job_id,at,message) VALUES(?,?,?,?)",
                (resource_id, job_id, time.time(), message),
            )

    def resource(self, resource_id):
        with self.connection() as db:
            row = db.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
        if row is None:
            raise KeyError("Compute resource not found")
        return json.loads(row["body"])

    def resources(self):
        with self.connection() as db:
            return [
                json.loads(row["body"])
                for row in db.execute("SELECT body FROM resources ORDER BY id")
            ]

    def save_resource(self, resource):
        with self.connection() as db:
            db.execute(
                "INSERT INTO resources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (resource["id"], json.dumps(resource)),
            )
        return resource

    def create_resource(self, spec: ResourceSpec, resource_id=None):
        if not spec.token:
            raise ValueError("A connection token is required to attach compute")
        resource = {
            **spec.model_dump(exclude={"token", "url"}),
            "id": resource_id or uuid4().hex,
            "desired": "running",
            "created_at": time.time(),
            "last_error": None,
        }
        self.save_resource(resource)
        self.save_attachment(
            {
                "resource_id": resource["id"],
                "url": spec.url,
                "token": spec.token,
                "state": "connecting",
                "checked": 0.0,
                "last_used": time.time(),
            }
        )
        self.event(resource["id"], "Compute attached")
        return resource

    def attachment(self, resource_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT body FROM attachments WHERE resource_id=?", (resource_id,)
            ).fetchone()
        return json.loads(row["body"]) if row else None

    def attachments(self):
        with self.connection() as db:
            return [json.loads(row["body"]) for row in db.execute("SELECT body FROM attachments")]

    def save_attachment(self, attachment):
        with self.connection() as db:
            db.execute(
                "INSERT INTO attachments VALUES(?,?) ON CONFLICT (resource_id) DO UPDATE SET "
                "body=excluded.body",
                (attachment["resource_id"], json.dumps(attachment)),
            )

    def remove_attachment(self, resource_id):
        with self.connection() as db:
            db.execute("DELETE FROM attachments WHERE resource_id=?", (resource_id,))

    def submit(self, request: JobRequest, resource_id):
        body = request.model_dump()
        body["resource_id"] = resource_id
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        job_id = uuid4().hex
        created = time.time()
        with self.connection() as db:
            if self.backend:  # serialize submitters, as BEGIN IMMEDIATE does for SQLite
                db.conn.execute("LOCK TABLE jobs IN SHARE ROW EXCLUSIVE MODE")
            else:
                db.execute("BEGIN IMMEDIATE")
            if request.idempotency_key:
                row = db.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?", (request.idempotency_key,)
                ).fetchone()
                if row:
                    if row["fingerprint"] != fingerprint:
                        raise ValueError("Idempotency key already belongs to a different request")
                    return self.decode_job(row)
            resource = json.loads(
                db.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()[
                    "body"
                ]
            )
            if resource["desired"] != "running":
                raise ValueError("Compute resource is stopped; start it before submitting work")
            count = db.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE resource_id=? AND state IN ('queued','running')",
                (resource_id,),
            ).fetchone()["n"]
            if count >= resource["max_queue"]:
                raise ValueError("Compute queue is full")
            db.execute(
                "INSERT INTO jobs(id,resource_id,state,created,available,fingerprint,idempotency_key,body) VALUES(?,?,'queued',?,?,?,?,?)",
                (
                    job_id,
                    resource_id,
                    created,
                    created,
                    fingerprint,
                    request.idempotency_key,
                    json.dumps(body),
                ),
            )
        self.event(resource_id, "Job queued", job_id)
        return self.job(job_id)

    @staticmethod
    def decode_job(row, private=False):
        job = dict(row)
        body = json.loads(job.pop("body"))
        job.update(operation=body["operation"], max_attempts=body["max_attempts"])
        job.pop("fingerprint")
        if private:
            job["payload"] = body["payload"]
            job["queue_timeout_seconds"] = body["queue_timeout_seconds"]
            job["timeout_seconds"] = body.get("timeout_seconds")
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def job(self, job_id, private=False):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Compute job not found")
        return self.decode_job(row, private)

    def jobs(self, state=None, limit=100):
        with self.connection() as db:
            rows = db.execute(
                "SELECT id,resource_id,state,created,available,attempts,cancel,fingerprint,idempotency_key,body,NULL AS result,error,instance_id,started,finished FROM jobs"
                + (" WHERE state=?" if state else "")
                + " ORDER BY created DESC LIMIT ?",
                (state, limit) if state else (limit,),
            ).fetchall()
        return [self.decode_job(row) for row in rows]

    def claim(self, job_id, instance_id):
        with self.connection() as db:
            changed = db.execute(
                "UPDATE jobs SET state='running',attempts=attempts+1,instance_id=?,started=?,error=NULL WHERE id=? AND state='queued' AND cancel=0 AND available<=?",
                (instance_id, time.time(), job_id, time.time()),
            ).rowcount
            if changed:
                row = db.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,?)",
                    (
                        job_id,
                        row["attempts"],
                        json.dumps(
                            {"started": time.time(), "instance_id": instance_id, "state": "running"}
                        ),
                    ),
                )
        return bool(changed)

    def finish(self, job_id, state, result=None, error=None, retry=False, expected_attempt=None):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if expected_attempt is not None and row["attempts"] != expected_attempt:
                return
            if row["state"] not in {"queued", "running"}:
                return
            state = "cancelled" if row["cancel"] else state
            if state == "cancelled":
                result = None
            retry = retry and not row["cancel"]
            if row["attempts"]:
                attempt = {
                    "started": row["started"],
                    "finished": time.time(),
                    "state": state,
                    "instance_id": row["instance_id"],
                    "error": error,
                }
                db.execute(
                    "UPDATE attempts SET body=? WHERE job_id=? AND number=?",
                    (json.dumps(attempt), job_id, row["attempts"]),
                )
            db.execute(
                "UPDATE jobs SET state=?,result=?,error=?,finished=?,available=? WHERE id=?",
                (
                    "queued" if retry else state,
                    json.dumps(result) if result is not None else None,
                    error,
                    None if retry else time.time(),
                    time.time() + min(2 ** row["attempts"], 30),
                    job_id,
                ),
            )
        self.event(row["resource_id"], "Job " + ("retry queued" if retry else state), job_id)

    def cancel(self, job_id):
        self.job(job_id)
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET cancel=1 WHERE id=? AND state IN ('queued','running')", (job_id,)
            )
        job = self.job(job_id)
        if job["state"] == "queued":
            self.finish(job_id, "cancelled")
        return self.job(job_id)

    def history(self, resource_id=None, job_id=None):
        with self.connection() as db:
            if job_id:
                return [
                    json.loads(row["body"])
                    for row in db.execute(
                        "SELECT body FROM attempts WHERE job_id=? ORDER BY number", (job_id,)
                    )
                ]
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM events WHERE resource_id=? ORDER BY id DESC LIMIT 100",
                    (resource_id,),
                )
            ]
