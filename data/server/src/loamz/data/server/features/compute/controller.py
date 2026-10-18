"""Durable batch-job scheduler for attached compute, independent of the catalog/API.

The controller owns routing, queueing and job history only. It never provisions, starts, sizes
or terminates compute: every resource is an externally operated endpoint attached by address and
connection secret. Interactive statements do not pass through here; the control plane signs
tickets and talks to SQL workers directly.
"""

from __future__ import annotations

import fcntl
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from loamz.data.server.features.compute.manifests import (
    DEFAULT_TIMEOUT,
    JOB_MODELS,
    OPERATION_ROLE,
    JobEnvelope,
    storage_identity,
)
from loamz.data.server.features.compute.models import TERMINAL, JobRequest, resource_kind
from loamz.data.server.features.compute.store import ComputeStore
from loamz.data.server.kernel.settings import Settings
from loamz.data.server.kernel.tls import internal_client_context

logger = logging.getLogger(__name__)

READY_PROBE_SECONDS = 5.0
RETRY_PROBE_SECONDS = 1.0
JOB_POLL_SECONDS = 0.5
IDLE_TICK_SECONDS = 1.0
ELECTION_SECONDS = 1.0


class ComputeController:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = ComputeStore(
            settings.controller_state / "state.sqlite",
            url=settings.controller_state_url.get_secret_value(),
        )
        self.identity = storage_identity(settings)
        self.store.bind_storage(self.identity)
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        # Set on submit/cancel/resume so dispatch happens immediately instead of on a timer.
        self.wake = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=64)
        self.http = httpx.Client(
            trust_env=False, timeout=10, verify=internal_client_context(settings)
        )
        self.futures = {}
        self.probes = {}
        self.thread = None
        self.lease = None
        self.is_leader = False
        self.election = None
        self.election_thread = None

    @property
    def active(self) -> bool:
        """Whether this process is the one scheduler (always true for a single, file-locked node)."""
        return self.is_leader

    def start(self):
        if self.store.shared:
            # Several controllers may run against one PostgreSQL database; one is elected, the others
            # wait and take over (with the same recovery as a restart) if it disappears.
            self.election = self.store.leader("controller")
            self.election_thread = threading.Thread(
                target=self.elect, name="compute-election", daemon=True
            )
            self.election_thread.start()
            return
        lease_path = self.settings.controller_state / "controller.lock"
        self.lease = open(lease_path, "a+")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            raise ValueError(
                "Another compute controller already owns this state directory"
            ) from None
        self.promote()

    def promote(self):
        """Become the scheduler. The controller cannot terminate attached compute; it asks for
        interrupted executions to stop (best effort) before revoking their lease and replaying.
        Writes use a fresh write id per attempt and are only published by the control plane, so a
        late duplicate attempt leaves unreferenced files rather than corrupting a table."""
        for job in self.store.jobs("running", 10000):
            self.interrupt(job)
            self.store.finish(
                job["id"],
                "failed",
                error="Controller restarted; previous execution lease revoked",
                retry=job["attempts"] < job["max_attempts"],
            )
        self.is_leader = True
        self.thread = threading.Thread(target=self.loop, name="compute-scheduler", daemon=True)
        self.thread.start()

    def demote(self):
        """Stop scheduling at once: another controller may now be dispatching the same jobs."""
        self.is_leader = False
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=35)
        with self.lock:
            for future in self.futures.values():
                future.cancel()
            self.futures.clear()
        logger.warning("Lost the compute controller leadership; standing by")

    def elect(self):
        while not self.shutdown.is_set():
            held = self.election.acquire()
            if held and not self.is_leader:
                logger.info("This controller is now the active scheduler")
                self.promote()
            elif not held and self.is_leader:
                self.demote()
            self.shutdown.wait(ELECTION_SECONDS)

    def close(self):
        self.shutdown.set()
        self.wake.set()
        if self.election_thread:
            self.election_thread.join(timeout=5)
        if self.thread:
            self.thread.join(timeout=35)
        with self.lock:
            if self.is_leader:
                for job in self.store.jobs("running", 10000):
                    self.interrupt(job)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.http.close()
        if self.lease:
            self.lease.close()
        if self.election:
            self.election.release()  # hand over at once instead of waiting for the connection to time out
        self.store.close()

    def resources(self):
        values = []
        for resource in self.store.resources():
            attachment = self.store.attachment(resource["id"]) or {}
            if resource["desired"] == "stopped":
                state = "stopped"
            elif attachment.get("state") == "ready":
                state = "ready"
            elif attachment.get("state") == "unreachable":
                state = "error"
            else:
                state = "starting"
            values.append(
                {
                    **resource,
                    "kind": resource_kind(resource),
                    "state": state,
                    "url": attachment.get("url"),
                    "checked": attachment.get("checked"),
                }
            )
        return values

    def action(self, resource_id, action):
        with self.lock:
            resource = self.store.resource(resource_id)
            if action == "delete":
                if resource["desired"] != "stopped":
                    raise ValueError("Pause the resource before detaching it")
                if any(
                    j["resource_id"] == resource_id and j["state"] not in TERMINAL
                    for j in self.store.jobs(limit=10000)
                ):
                    raise ValueError("Cancel pending jobs before detaching the resource")
                with self.store.connection() as db:
                    db.execute("DELETE FROM resources WHERE id=?", (resource_id,))
                self.store.remove_attachment(resource_id)
                self.store.event(resource_id, "Compute detached")
                return {"id": resource_id, "state": "deleted"}
            if action == "stop":
                resource["desired"] = "stopped"
                self.store.save_resource(resource)
                for job in self.store.jobs(limit=10000):
                    if job["resource_id"] == resource_id and job["state"] not in TERMINAL:
                        self.cancel(job["id"])
                self.store.event(resource_id, "Routing paused; attached compute left running")
            elif action == "start":
                resource.update(desired="running", last_error=None)
                self.store.save_resource(resource)
                attachment = self.store.attachment(resource_id)
                if attachment:
                    attachment.update(state="connecting", checked=0.0)
                    self.store.save_attachment(attachment)
                self.store.event(resource_id, "Routing resumed")
                self.wake.set()
            else:
                raise ValueError("Unsupported resource action")
            return next(r for r in self.resources() if r["id"] == resource_id)

    def reattach(self, resource_id, url, token):
        """Replace the endpoint/secret of a paused resource."""
        attachment = self.store.attachment(resource_id)
        attachment.update(url=url, state="connecting", checked=0.0)
        if token:
            attachment["token"] = token
        self.store.save_attachment(attachment)

    def submit(self, request: JobRequest):
        role = OPERATION_ROLE[request.operation]
        with self.lock:
            if request.resource_id:
                resource = self.store.resource(request.resource_id)
            else:
                candidates = [
                    r
                    for r in self.store.resources()
                    if r["role"] == role and r["desired"] == "running"
                ]
                ready = [
                    r
                    for r in candidates
                    if (self.store.attachment(r["id"]) or {}).get("state") == "ready"
                ]
                resource = next(iter(ready or candidates), None)
                if resource is None:
                    raise ValueError(f"No attached compute serves {request.operation} jobs")
            if resource["role"] != role:
                raise ValueError("Compute resource does not support this workload")
            JOB_MODELS[request.operation].model_validate(request.payload).check(self.settings)
            job = self.store.submit(request, resource["id"])
        self.wake.set()
        return job

    def call(self, attachment, method, path, payload=None, timeout=5.0):
        return self.http.request(
            method,
            attachment["url"] + path,
            json=payload,
            headers={"Authorization": "Bearer " + attachment["token"]},
            timeout=timeout,
        )

    def interrupt(self, job):
        """Best-effort request that attached compute stop one execution."""
        attachment = self.store.attachment(job["resource_id"])
        if not attachment:
            return
        try:
            self.call(attachment, "POST", f"/internal/v1/jobs/{job['id']}:cancel", timeout=1)
        except httpx.HTTPError:
            pass

    def cancel(self, job_id):
        with self.lock:
            job = self.store.cancel(job_id)
            if job["state"] == "running":
                self.interrupt(job)
            return self.store.job(job_id)

    def execute(self, job, attachment):
        """Start one attempt on the worker, then follow it to completion."""
        job_id = job["id"]
        attempt = job["attempts"]

        def finish(*args, **kwargs):
            return self.store.finish(*args, expected_attempt=attempt, **kwargs)

        retry = attempt < job["max_attempts"]
        timeout = job.get("timeout_seconds") or DEFAULT_TIMEOUT[job["operation"]]
        envelope = JobEnvelope(
            execution_id=job_id,
            operation=job["operation"],
            storage_id=self.identity,
            timeout_seconds=timeout,
            payload=job["payload"],
        ).model_dump()
        try:
            if self.store.job(job_id)["cancel"]:
                finish(job_id, "cancelled")
                return
            response = self.call(attachment, "POST", "/internal/v1/jobs", envelope, timeout=10)
            if response.status_code != 202:
                detail = _detail(response)
                finish(
                    job_id,
                    "failed",
                    error=detail,
                    retry=retry and response.status_code in {429, 503},
                )
                return
            deadline = time.monotonic() + timeout + 60
            while not self.shutdown.is_set():
                if self.store.job(job_id)["cancel"]:
                    self.interrupt(job)
                    finish(job_id, "cancelled")
                    return
                if time.monotonic() > deadline:
                    self.interrupt(job)
                    finish(job_id, "failed", error="Job exceeded its time limit")
                    return
                response = self.call(attachment, "GET", f"/internal/v1/jobs/{job_id}", timeout=5)
                if response.status_code == 404:
                    finish(job_id, "failed", error="Attached compute lost the job", retry=retry)
                    return
                body = response.json()
                if body["state"] == "succeeded":
                    finish(job_id, "succeeded", result=body["result"])
                    return
                if body["state"] in {"failed", "cancelled"}:
                    finish(job_id, body["state"], error=body.get("error"))
                    return
                time.sleep(JOB_POLL_SECONDS)
        except (httpx.HTTPError, ValueError) as exc:
            finish(job_id, "failed", error="Attached compute connection lost", retry=retry)
            logger.info("Compute request ended: %s", type(exc).__name__)

    def check(self, resource_id):
        """Probe one attachment outside the scheduler lock, then record the result."""
        attachment = self.store.attachment(resource_id)
        if attachment is None:
            return
        try:
            resource = self.store.resource(resource_id)
        except KeyError:
            return
        state, error = "ready", None
        try:
            response = self.call(attachment, "GET", "/internal/v1/health", timeout=1)
            body = response.json() if response.status_code == 200 else {}
            if response.status_code == 401:
                state, error = "unreachable", "Attached compute rejected the connection token"
            elif not body:
                state, error = "unreachable", "Attached compute is not healthy"
            elif body.get("storage_id") != self.identity:
                state, error = "unreachable", "Attached compute uses a different storage binding"
            elif body.get("role") != resource["role"]:
                state, error = "unreachable", "Attached compute serves a different workload role"
        except httpx.HTTPError, ValueError:
            state, error = "unreachable", "Attached compute endpoint is unreachable"
        with self.lock:
            latest = self.store.attachment(resource_id)
            if latest is None or latest["url"] != attachment["url"]:
                return
            changed = latest["state"] != state
            try:
                current = self.store.resource(resource_id)
            except KeyError:
                return
            # Readers do not take the scheduler lock: publish the reason before the state.
            if current["last_error"] != error:
                current["last_error"] = error
                self.store.save_resource(current)
            latest.update(state=state, checked=time.time())
            self.store.save_attachment(latest)
            if changed:
                self.store.event(resource_id, "Compute ready" if state == "ready" else error)
                self.wake.set()

    def loop(self):
        while not self.shutdown.is_set() and self.is_leader:
            self.wake.wait(IDLE_TICK_SECONDS)
            self.wake.clear()
            try:
                self.tick()
            except Exception:
                logger.exception("Compute reconciliation failed")

    def tick(self):
        with self.lock:
            now = time.time()
            for job_id, future in list(self.futures.items()):
                if future.done():
                    try:
                        future.result()
                    except Exception:
                        logger.exception("Job dispatcher failed")
                        self.store.finish(job_id, "failed", error="Internal job dispatcher failure")
                    self.futures.pop(job_id)
            running = self.store.jobs("running", 10000)
            queued = [
                self.store.job(j["id"], private=True)
                for j in reversed(self.store.jobs("queued", 10000))
            ]
            for job in queued:
                if now - job["created"] > job["queue_timeout_seconds"]:
                    self.store.finish(job["id"], "failed", error="Queue deadline exceeded")
            queued = [
                j
                for j in queued
                if now - j["created"] <= j["queue_timeout_seconds"] and j["available"] <= now
            ]
            for resource in self.store.resources():
                attachment = self.store.attachment(resource["id"])
                if attachment is None or resource["desired"] != "running":
                    continue
                interval = (
                    READY_PROBE_SECONDS if attachment["state"] == "ready" else RETRY_PROBE_SECONDS
                )
                probe = self.probes.get(resource["id"])
                if (probe is None or probe.done()) and now - attachment["checked"] >= interval:
                    self.probes[resource["id"]] = self.pool.submit(self.check, resource["id"])
                if attachment["state"] != "ready":
                    continue
                pending = [j for j in queued if j["resource_id"] == resource["id"]]
                load = sum(1 for j in running if j["resource_id"] == resource["id"])
                for job in pending[: max(0, resource["concurrency"] - load)]:
                    if self.store.claim(job["id"], resource["id"]):
                        current = self.store.job(job["id"], private=True)
                        self.futures[job["id"]] = self.pool.submit(
                            self.execute, current, attachment
                        )


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", "Compute rejected the job"))[:1200]
    except ValueError:
        return f"Compute rejected the job ({response.status_code})"
