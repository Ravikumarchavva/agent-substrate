"""Worker data plane: executes signed statement tickets and controller-dispatched batch jobs.

A worker never opens the control-plane catalog and never sees user credentials. Statements
arrive as Ed25519-signed tickets naming the exact files to read; DuckDB is locked so that, even
if a statement were crafted, it can read only inside the warehouse bucket. Batch jobs run in
child processes forked from a warm server so they can be cancelled and memory-isolated.
"""

from __future__ import annotations

import hashlib
import multiprocessing
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import duckdb
import pyarrow as pa
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from loamz.data.server.features.compute.manifests import JOB_MODELS, JobEnvelope, storage_identity
from loamz.data.server.kernel import problems as http
from loamz.data.server.kernel.errors import public_error
from loamz.data.server.kernel.settings import Settings
from loamz.data.server.kernel.tickets import TicketVerifier
from loamz.data.storage.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    QuotaExceededError,
    RateLimitedError,
    UnauthenticatedError,
)

Role = Literal["sql", "jobs", "documents"]
ARROW = "application/vnd.apache.arrow.stream"
BATCH_ROWS = 65_536
JOB_RESULT_TTL_SECONDS = 3600


def quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class _Collector:
    """File-like sink that hands each written IPC chunk to the HTTP response."""

    closed = False

    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def write(self, data: Any) -> int:
        self.parts.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data, self.parts = b"".join(self.parts), []
        return data


class ResultCache:
    """Recent small results, by plan hash. A key covers the SQL and the exact immutable files it
    reads, so a hit is always current: a commit changes the file list and therefore the key."""

    TTL_SECONDS = 600

    def __init__(self, capacity: int, entry_limit: int):
        self.capacity, self.entry_limit = capacity, entry_limit
        self.lock = threading.Lock()
        self.items: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.size = 0
        self.hits = self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.capacity > 0

    def stats(self) -> dict[str, int]:
        with self.lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "entries": len(self.items),
                "bytes": self.size,
            }

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        with self.lock:
            entry = self.items.get(key)
            if entry is None or entry[0] < time.monotonic():
                if entry is not None:
                    self.size -= len(entry[1])
                    del self.items[key]
                self.misses += 1
                return None
            self.items.move_to_end(key)
            self.hits += 1
            return entry[1]

    def put(self, key: str, value: bytes) -> None:
        if not self.enabled or len(value) > self.entry_limit:
            return
        with self.lock:
            if (old := self.items.pop(key, None)) is not None:
                self.size -= len(old[1])
            self.items[key] = (time.monotonic() + self.TTL_SECONDS, value)
            self.size += len(value)
            while self.size > self.capacity and self.items:
                _, (_, evicted) = self.items.popitem(last=False)
                self.size -= len(evicted)


class StatementEngine:
    """One persistent, locked-down DuckDB database; a cursor (connection) per statement."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = duckdb.connect(
            config={
                **({"threads": settings.worker_threads} if settings.worker_threads else {}),
                "memory_limit": settings.worker_memory_limit,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            }
        )
        try:
            self.db.execute("LOAD httpfs")
        except duckdb.Error:
            self.db.execute("INSTALL httpfs")
            self.db.execute("LOAD httpfs")
        self.db.execute("SET allow_persistent_secrets=false")  # secrets never touch disk
        scheme, _, host = settings.s3_endpoint_url.partition("://")
        key = settings.s3_access_key_id.get_secret_value()
        # Always point S3 reads at the configured endpoint, never DuckDB's default (AWS): with no
        # credentials a read is refused by our store instead of attempted against another one.
        for statement in (
            f"SET s3_endpoint={quote(host)}",
            "SET s3_url_style='path'",
            f"SET s3_use_ssl={'true' if scheme == 'https' else 'false'}",
            f"SET s3_region={quote(settings.s3_region)}",
        ):
            self.db.execute(statement)
        if key:
            self.db.execute(
                "CREATE SECRET warehouse (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT ?, URL_STYLE 'path', "
                "USE_SSL ?, REGION ?, SCOPE ?)",
                [
                    key,
                    settings.s3_secret_access_key.get_secret_value(),
                    host,
                    scheme == "https",
                    settings.s3_region,
                    f"s3://{settings.warehouse}/",
                ],
            )
        # Per-statement credentials arrive in tickets as temporary secrets; the secret manager must
        # be initialised before the configuration is locked.
        self.db.execute("SELECT count(*) FROM duckdb_secrets()").fetchall()
        self.vended: dict[str, float] = {}  # secret name -> credential expiry
        for statement in (
            "SET parquet_metadata_cache=true",
            "SET enable_http_metadata_cache=true",
            f"SET allowed_directories=[{quote(f's3://{settings.warehouse}/')}]",
            "SET enable_external_access=false",
            "SET lock_configuration=true",
        ):
            self.db.execute(statement)
        self.gate = threading.BoundedSemaphore(settings.worker_concurrency)
        self.lock = threading.Lock()
        self.active: dict[str, duckdb.DuckDBPyConnection] = {}
        self.projections: OrderedDict[tuple, str] = OrderedDict()
        self.results = ResultCache(
            settings.worker_result_cache_mb * 2**20, settings.worker_result_cache_entry_mb * 2**20
        )

    def _projection(self, cursor: duckdb.DuckDBPyConnection, table: dict[str, Any]) -> str:
        """SELECT list mapping the files onto the table schema (added columns become NULL)."""
        files = table["files"]
        key = (table["table"], table["snapshot"], hashlib.sha1("\n".join(files).encode()).digest())
        with self.lock:
            cached = self.projections.get(key)
        if cached:
            return cached
        # Folder names are layout, not data: never infer columns from them.
        source = f"read_parquet([{', '.join(map(quote, files))}], union_by_name=true, hive_partitioning=false)"
        present = {
            row[0].lower(): row[1]
            for row in cursor.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
        }
        columns = []
        for name, kind in table["columns"]:
            found = present.get(name.lower())
            if found is None:
                columns.append(f"CAST(NULL AS {kind}) AS {ident(name)}")
            elif found == kind:
                columns.append(ident(name))
            else:
                columns.append(f"CAST({ident(name)} AS {kind}) AS {ident(name)}")
        projection = f"SELECT {', '.join(columns)} FROM {source}"
        with self.lock:
            self.projections[key] = projection
            while len(self.projections) > 1024:
                self.projections.popitem(last=False)
        return projection

    def _install_credentials(self, storage: dict[str, Any] | None) -> None:
        """Make a ticket's vended credentials available to DuckDB, scoped to their folders.

        Secrets are shared by the database, which is safe: they only permit reading the table
        folders they were issued for, and a statement can only name files in its own ticket.
        """
        if not storage:
            return
        name = "v_" + hashlib.sha1(storage["key_id"].encode()).hexdigest()[:20]
        with self.lock:
            now = time.time()
            for old, expires in list(self.vended.items()):
                if expires < now:
                    del self.vended[old]
                    self.db.cursor().execute(f"DROP SECRET IF EXISTS {ident(old)}")
            if name in self.vended:
                return
            scheme, _, host = self.settings.s3_endpoint_url.partition("://")
            scopes = ", ".join(quote(s) for s in storage["scopes"])
            self.db.cursor().execute(
                f"CREATE TEMPORARY SECRET {ident(name)} (TYPE S3, KEY_ID ?, SECRET ?, SESSION_TOKEN ?, "
                f"ENDPOINT ?, URL_STYLE 'path', USE_SSL ?, REGION ?, SCOPE [{scopes}])",
                [
                    storage["key_id"],
                    storage["secret"],
                    storage["token"],
                    host,
                    scheme == "https",
                    self.settings.s3_region,
                ],
            )
            self.vended[name] = float(storage["expires"])

    def _register(self, cursor: duckdb.DuckDBPyConnection, tables: list[dict[str, Any]]) -> None:
        for table in tables:
            if table["files"]:
                body = self._projection(cursor, table)
            else:
                body = (
                    "SELECT "
                    + ", ".join(
                        f"CAST(NULL AS {kind}) AS {ident(name)}" for name, kind in table["columns"]
                    )
                    + " LIMIT 0"
                )
            cursor.execute(f"CREATE TEMP VIEW {ident(table['alias'])} AS {body}")

    def _open(self, claims: dict[str, Any]):
        """Take a slot, register the ticket's files and start the statement; returns the cursor,
        its Arrow reader, the first batch and the timeout timer. Errors surface here as 400s."""
        if not self.gate.acquire(timeout=2):
            raise RateLimitedError("Compute is at capacity; retry shortly", retry_after=1)
        statement_id = claims["jti"]
        cursor = self.db.cursor()
        timer = threading.Timer(claims["limits"]["timeout_ms"] / 1000, cursor.interrupt)
        try:
            with self.lock:
                self.active[statement_id] = cursor
            timer.start()
            self._install_credentials(claims.get("storage"))
            self._register(cursor, claims["tables"])
            reader = cursor.execute(claims["sql"]).to_arrow_reader(BATCH_ROWS)
            first = next(iter(reader), None)
        except BaseException:
            self._finish(statement_id, cursor, timer)
            raise
        return statement_id, cursor, reader, first, timer

    def execute(self, claims: dict[str, Any]) -> tuple[Iterator[bytes], bool]:
        """Run a ticket. Returns the Arrow IPC byte stream and whether it came from the cache."""
        limits = claims["limits"]
        key = claims.get("cache")
        if key and (hit := self.results.get(key)) is not None and len(hit) <= limits["max_bytes"]:
            return iter([hit]), True
        statement_id, cursor, reader, first, timer = self._open(claims)

        def stream() -> Iterator[bytes]:
            sink = _Collector()
            sent = 0
            kept: list[bytes] | None = [] if key and self.results.enabled else None
            try:
                with pa.ipc.new_stream(pa.PythonFile(sink, mode="w"), reader.schema) as writer:
                    batches = [first] if first is not None else []
                    for batch in _chain(batches, reader):
                        writer.write_batch(batch)
                        chunk = sink.take()
                        sent += len(chunk)
                        if sent > limits["max_bytes"]:
                            raise QuotaExceededError(
                                "Result exceeds the byte limit; aggregate in SQL"
                            )
                        if kept is not None:
                            kept.append(chunk)
                            if sent > self.results.entry_limit:
                                kept = None  # too big to remember
                        yield chunk
                tail = sink.take()
                if kept is not None:
                    kept.append(tail)
                    self.results.put(key, b"".join(kept))
                yield tail
            finally:
                self._finish(statement_id, cursor, timer)

        return stream(), False

    def batches(self, claims: dict[str, Any]) -> Iterator[pa.RecordBatch]:
        """The statement's Arrow batches, for batch jobs that write them to storage."""
        statement_id, cursor, reader, first, timer = self._open(claims)
        try:
            yield from _chain([first] if first is not None else [], reader)
        finally:
            self._finish(statement_id, cursor, timer)

    def schema(self, claims: dict[str, Any]) -> pa.Schema:
        statement_id, cursor, reader, _, timer = self._open(claims)
        try:
            return reader.schema
        finally:
            self._finish(statement_id, cursor, timer)

    def _finish(
        self, statement_id: str, cursor: duckdb.DuckDBPyConnection, timer: threading.Timer
    ) -> None:
        timer.cancel()
        with self.lock:
            self.active.pop(statement_id, None)
        try:
            cursor.close()
        finally:
            self.gate.release()

    def cancel(self, statement_id: str) -> bool:
        with self.lock:
            cursor = self.active.get(statement_id)
        if cursor is None:
            return False
        cursor.interrupt()
        return True


def _chain(first: list[pa.RecordBatch], reader: pa.RecordBatchReader) -> Iterator[pa.RecordBatch]:
    yield from first
    yield from reader


def available_memory() -> int:
    """Memory this worker may use: its container limit if it has one, else the host's."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = int(open(path).read().strip())
        except OSError, ValueError:
            continue  # "max" (unlimited) or no cgroup here
        if value < 2**50:
            return value
    with open("/proc/meminfo") as meminfo:
        return int(meminfo.readline().split()[1]) * 1024


def memory_ceiling(settings: Settings) -> int:
    return settings.worker_job_memory_mb * 2**20 or available_memory() // 2


def rss_bytes(pid: int) -> int:
    """Resident memory of a process (0 if it is gone or this is not Linux)."""
    try:
        with open(f"/proc/{pid}/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError, ValueError:
        pass
    return 0


def run_job(pipe: Any, settings: Settings, operation: str, payload: dict[str, Any]) -> None:
    """Child process entry: one job, its own engines, direct object-store access."""
    try:
        if operation == "infer":
            from loamz.data.server.features.compute.batch import infer

            result = infer(payload, settings)
        elif operation == "ingest":
            from loamz.data.server.features.compute.batch import ingest

            result = ingest(payload, settings)
        elif operation == "transform":
            from loamz.data.server.features.compute.batch import transform

            result = transform(payload, settings)
        elif operation == "optimize":
            from loamz.data.server.features.compute.batch import optimize

            result = optimize(payload, settings)
        elif operation == "materialize":
            from loamz.data.server.features.compute.batch import materialize

            result = materialize(payload, settings)
        elif operation == "delete":
            from loamz.data.server.features.compute.batch import delete

            result = delete(payload, settings)
        elif operation == "chunk":
            text = payload["text"]
            result = {"chunks": [text[i : i + 1000] for i in range(0, len(text), 800)]}
        else:
            raise ValueError("Unknown operation")
        pipe.send(("ok", result))
    except Exception as exc:  # reported to the controller, never raised across the process
        pipe.send(("error", public_error(exc, settings)))
    finally:
        pipe.close()


class JobRunner:
    """Asynchronous batch jobs: start returns at once, the controller polls for the outcome."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.context = multiprocessing.get_context("forkserver")
        # Children fork from a server that has already imported the heavy engines.
        self.context.set_forkserver_preload(
            ["polars", "pyarrow", "pyarrow.parquet", "loamz.data.server.features.compute.batch"]
        )
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.gate = threading.BoundedSemaphore(settings.worker_concurrency)
        self.ceiling = memory_ceiling(settings)

    def start(
        self, execution_id: str, operation: str, payload: dict[str, Any], timeout: float
    ) -> dict:
        with self.lock:
            now = time.time()
            self.jobs = {
                k: v
                for k, v in self.jobs.items()
                if v["state"] == "running" or now - v["finished"] < JOB_RESULT_TTL_SECONDS
            }
            if self.jobs.get(execution_id, {}).get("state") == "running":
                return self.public(execution_id)  # idempotent start of the same attempt
            if not self.gate.acquire(blocking=False):
                raise RateLimitedError("Compute is at capacity; retry shortly", retry_after=2)
            receiver, sender = self.context.Pipe(duplex=False)
            process = self.context.Process(
                target=run_job, args=(sender, self.settings, operation, payload), daemon=True
            )
            job = {
                "state": "running",
                "process": process,
                "receiver": receiver,
                "result": None,
                "error": None,
                "started": now,
                "finished": 0.0,
                "deadline": time.monotonic() + timeout,
            }
            self.jobs[execution_id] = job
        try:
            process.start()
        except BaseException:
            self.gate.release()
            raise
        finally:
            sender.close()
        threading.Thread(target=self._watch, args=(execution_id,), daemon=True).start()
        return self.public(execution_id)

    def _watch(self, execution_id: str) -> None:
        job = self.jobs[execution_id]
        process, receiver = job["process"], job["receiver"]
        try:
            while not receiver.poll(0.2):
                if job["state"] == "cancelled" or time.monotonic() > job["deadline"]:
                    if job["state"] != "cancelled":
                        job.update(state="failed", error="Job exceeded its time limit")
                    return
                if rss_bytes(process.pid) > self.ceiling:
                    job.update(
                        state="failed",
                        error=(
                            f"Job needed more than {self.ceiling / 2**30:.1f} GiB of memory and was "
                            "stopped to protect the machine. Run it on a smaller slice (for "
                            "maintenance, use `where` to go partition by partition) or give the "
                            "worker more memory (LOAMZ_DATA_WORKER_JOB_MEMORY_MB)."
                        ),
                    )
                    return
                if not process.is_alive():
                    job.update(state="failed", error="Job process exited without a result")
                    return
            try:
                status, value = receiver.recv()
            except EOFError:
                job.update(state="failed", error="Job process exited without a result")
                return
            if status == "ok":
                job.update(state="succeeded", result=value)
            else:
                job.update(state="failed", error=value)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(2)
                if process.is_alive():
                    process.kill()
            process.join(1)
            receiver.close()
            job["finished"] = time.time()
            self.gate.release()

    def cancel(self, execution_id: str) -> bool:
        job = self.jobs.get(execution_id)
        if not job or job["state"] != "running":
            return False
        job["state"] = "cancelled"
        return True

    def public(self, execution_id: str) -> dict[str, Any]:
        job = self.jobs.get(execution_id)
        if job is None:
            raise NotFoundError("Job is not known to this worker")
        return {
            "execution_id": execution_id,
            "state": job["state"],
            "result": job["result"],
            "error": job["error"],
        }


def create_compute_app(settings: Settings, role: Role) -> FastAPI:
    token = settings.worker_token.get_secret_value()
    if len(token) < 32:
        raise ValueError("Workers require LOAMZ_DATA_WORKER_TOKEN (32+ characters)")
    serves_statements = role in {"sql", "jobs"}
    if serves_statements and settings.ticket_public_key_file is None:
        raise ValueError("SQL workers require LOAMZ_DATA_TICKET_PUBLIC_KEY_FILE")
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if serves_statements:
            state["engine"] = StatementEngine(settings)
            state["tickets"] = TicketVerifier(settings.ticket_public_key_file)
        if role != "sql":
            state["jobs"] = JobRunner(settings)
        yield

    app = FastAPI(
        title=f"Loamz Data {role} worker", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    identity = storage_identity(settings)

    http.install(app, settings)

    @app.exception_handler(duckdb.Error)
    async def query_error(request: Request, exc: duckdb.Error):
        kind = "statement-timeout" if isinstance(exc, duckdb.InterruptException) else "query-error"
        return http.problem(request, 400, kind, public_error(exc, settings))

    def controller_only(request: Request) -> None:
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise UnauthenticatedError("Unauthorized compute connection")

    def ticket(request: Request) -> dict[str, Any]:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme != "Ticket" or "tickets" not in state:
            raise UnauthenticatedError("A statement ticket is required")
        return state["tickets"].verify(value)

    @app.get("/livez")
    def livez():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        return {"status": "ready" if state else "starting"}

    @app.get("/internal/v1/health")
    def health(request: Request):
        controller_only(request)
        engine = state.get("engine")
        return {
            "status": "ok",
            "role": role,
            "storage_id": identity,
            "version": 1,
            **({"result_cache": engine.results.stats()} if engine else {}),
        }

    if serves_statements:

        @app.post("/internal/v1/statements")
        def statement(request: Request):
            claims = ticket(request)
            if claims.get("op") != "query":
                raise UnauthenticatedError("Ticket does not authorize a query")
            stream, cached = state["engine"].execute(claims)
            return StreamingResponse(
                stream,
                media_type=ARROW,
                headers={
                    "X-Statement-Id": claims["jti"],
                    "X-Result-Cache": "hit" if cached else "miss",
                },
            )

        @app.post("/internal/v1/statements/{statement_id}:cancel")
        def cancel_statement(statement_id: str, request: Request):
            claims = ticket(request)
            if claims.get("op") != "cancel" or claims.get("statement") != statement_id:
                raise UnauthenticatedError("Ticket does not authorize this cancellation")
            return {"interrupted": state["engine"].cancel(statement_id)}

    if role != "sql":
        operations = {
            "jobs": {"infer", "ingest", "transform", "optimize", "materialize", "delete"},
            "documents": {"chunk"},
        }[role]

        @app.post("/internal/v1/jobs", status_code=202)
        def start_job(body: JobEnvelope, request: Request):
            controller_only(request)
            if body.operation not in operations:
                raise InvalidRequestError(f"This worker does not run {body.operation} jobs")
            if body.storage_id != identity:
                raise ConflictError("Controller and worker storage bindings differ")
            payload = JOB_MODELS[body.operation].model_validate(body.payload)
            payload.check(settings)
            return state["jobs"].start(
                body.execution_id, body.operation, payload.model_dump(), body.timeout_seconds
            )

        @app.get("/internal/v1/jobs/{execution_id}")
        def job_state(execution_id: str, request: Request):
            controller_only(request)
            return state["jobs"].public(execution_id)

        @app.post("/internal/v1/jobs/{execution_id}:cancel")
        def cancel_job(execution_id: str, request: Request):
            controller_only(request)
            return {"interrupted": state["jobs"].cancel(execution_id)}

    return app
