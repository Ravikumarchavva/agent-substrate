from __future__ import annotations

import base64
import hashlib
import mimetypes
import shlex
import threading
from dataclasses import dataclass
from typing import Any, cast

from k8s_agent_sandbox import SandboxClient
from k8s_agent_sandbox.exceptions import SandboxRequestError
from k8s_agent_sandbox.models import SandboxLocalTunnelConnectionConfig

from .session_store import InMemorySessionStore, SandboxSession, SessionStore


@dataclass(slots=True)
class CodeInterpreterConfig:
    template: str = "python-sandbox-template"
    namespace: str = "default"
    sandbox_ready_timeout: int = 180
    request_timeout: int = 120
    server_port: int = 8888
    shutdown_after_seconds: int | None = None
    warmpool: str | None = None
    # Per-user persistent workspace (see capabilities/storage/workspace.py).
    # When workspace_pvc_claim is set, a per-user SandboxTemplate is generated
    # on first use, mounting only users/{user_id} of the shared RWX PVC —
    # this subPath is the isolation boundary between users' sandboxes.
    # Unset (default) preserves the old shared-template, ephemeral behavior.
    workspace_pvc_claim: str | None = None
    workspace_mount_path: str = "/app/workspace"
    # Kubernetes RuntimeClass for sandbox pods. "gvisor" routes them through
    # runsc, a user-space kernel that intercepts syscalls before they reach the
    # host kernel — the isolation Google itself uses for GKE Sandbox / Cloud Run
    # untrusted code, and it needs no nested virtualization (unlike Kata /
    # Firecracker). Empty = cluster default runtime (weaker: shared kernel).
    runtime_class_name: str = ""


class CodeInterpreterService:
    """Owns sandbox lifecycle and all runtime API calls."""

    def __init__(
        self,
        config: CodeInterpreterConfig | None = None,
        store: SessionStore | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or CodeInterpreterConfig()
        self.store = store or InMemorySessionStore()
        self.client = client or SandboxClient(
            connection_config=SandboxLocalTunnelConnectionConfig(
                server_port=self.config.server_port,
            ),
        )
        self._handles: dict[str, Any] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.RLock()
        self._user_template_names: set[str] = set()

    def run_code(
        self,
        thread_id: str,
        code: str,
        timeout: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock_for_thread(thread_id):
            sandbox, session = self._get_or_create_sandbox(thread_id, user_id=user_id)
            # session_id tells the pod's own execution cwd to become
            # sessions/{thread_id}/ within the mounted per-user workspace
            # (see sandbox_runtime.py::_session_run_dir) — a no-op when the
            # pod isn't on a workspace-mounted template (runs at its root).
            payload = {"code": code, "session_id": thread_id}
            data = self._runtime_json(
                sandbox,
                "POST",
                "ci/run",
                timeout=timeout or self.config.request_timeout,
                json=payload,
            )
            return self._with_session("run_code", thread_id, session, data)

    def run_command(
        self,
        thread_id: str,
        argv: list[str],
        timeout: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a shell command in the session's pod (the ``command`` tool path).

        Uses the in-pod ``/execute`` endpoint, which runs the argv with
        ``cwd=WORKSPACE_DIR`` — inside the per-user ``subPath`` mount, so the
        same isolation boundary as ``run_code`` applies.
        """
        with self._lock_for_thread(thread_id):
            sandbox, session = self._get_or_create_sandbox(thread_id, user_id=user_id)
            effective_timeout = timeout or self.config.request_timeout
            data = self._runtime_json(
                sandbox,
                "POST",
                "execute",
                timeout=effective_timeout,
                json={
                    "command": shlex.join(argv),
                    "timeout": effective_timeout,
                },
            )
            return self._with_session("run_command", thread_id, session, data)

    def upload_file(
        self,
        thread_id: str,
        filename: str,
        content_base64: str,
        overwrite: bool = True,
        timeout: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock_for_thread(thread_id):
            sandbox, session = self._get_or_create_sandbox(thread_id, user_id=user_id)
            payload = {
                "filename": filename,
                "content_base64": content_base64,
                "overwrite": overwrite,
            }
            data = self._runtime_json(
                sandbox,
                "POST",
                "ci/upload",
                timeout=timeout or self.config.request_timeout,
                json=payload,
            )
            return self._with_session("upload_file", thread_id, session, data)

    def download_file(
        self,
        thread_id: str,
        path: str,
        timeout: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock_for_thread(thread_id):
            sandbox, session = self._get_or_create_sandbox(thread_id, user_id=user_id)
            data = self._runtime_json(
                sandbox,
                "POST",
                "ci/download",
                timeout=timeout or self.config.request_timeout,
                json={"path": path},
            )
            file_data = data.get("file")
            if isinstance(file_data, dict):
                self._add_data_uri(file_data)
            return self._with_session("download_file", thread_id, session, data)

    def list_files(
        self,
        thread_id: str,
        path: str = ".",
        recursive: bool = False,
        timeout: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock_for_thread(thread_id):
            sandbox, session = self._get_or_create_sandbox(thread_id, user_id=user_id)
            data = self._runtime_json(
                sandbox,
                "POST",
                "ci/list",
                timeout=timeout or self.config.request_timeout,
                json={"path": path, "recursive": recursive},
            )
            return self._with_session("list_files", thread_id, session, data)

    def reset_session(
        self,
        thread_id: str,
        timeout: int | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock_for_thread(thread_id):
            sandbox, session = self._get_or_create_sandbox(thread_id, user_id=user_id)
            data = self._runtime_json(
                sandbox,
                "POST",
                "ci/reset",
                timeout=timeout or self.config.request_timeout,
            )
            return self._with_session("reset_session", thread_id, session, data)

    def status(self, thread_id: str) -> dict[str, Any]:
        session = self.store.get(thread_id)
        if session is None:
            return {
                "status": "ok",
                "action": "status",
                "thread_id": thread_id,
                "session_status": "not_started",
            }

        status = "unknown"
        message = ""
        try:
            with self._lock_for_thread(thread_id):
                sandbox = self._handles.get(thread_id)
                if sandbox is None:
                    sandbox = self.client.get_sandbox(
                        session.claim_name,
                        namespace=session.namespace,
                    )
                    self._handles[thread_id] = sandbox
            status, message = sandbox.status()
        except Exception as exc:  # pragma: no cover - depends on cluster state
            status = "unreachable"
            message = str(exc)

        return self._with_session(
            "status",
            thread_id,
            session,
            {"status": "ok", "session_status": status, "message": message},
        )

    def terminate_session(self, thread_id: str) -> dict[str, Any]:
        with self._lock_for_thread(thread_id):
            session = self.store.get(thread_id)
            sandbox = self._handles.pop(thread_id, None)
            if session is not None and sandbox is None:
                try:
                    sandbox = self.client.get_sandbox(
                        session.claim_name,
                        namespace=session.namespace,
                    )
                except Exception:
                    sandbox = None

            if sandbox is not None:
                sandbox.terminate()
            self.store.delete(thread_id)
            return {
                "status": "ok",
                "action": "terminate_session",
                "thread_id": thread_id,
                "message": "Sandbox session terminated.",
            }

    def list_sessions(self) -> dict[str, Any]:
        sessions = [session.to_dict() for session in self.store.list()]
        return {"status": "ok", "action": "list_sessions", "sessions": sessions}

    def _get_or_create_sandbox(
        self, thread_id: str, *, user_id: str | None = None
    ) -> tuple[Any, SandboxSession]:
        existing_session = self.store.get(thread_id)
        existing_handle = self._handles.get(thread_id)
        if existing_session is not None and self._is_handle_active(existing_handle):
            return existing_handle, existing_session

        if existing_session is not None:
            try:
                sandbox = self.client.get_sandbox(
                    existing_session.claim_name,
                    namespace=existing_session.namespace,
                )
                self._handles[thread_id] = sandbox
                existing_session.sandbox_id = getattr(
                    sandbox,
                    "sandbox_id",
                    existing_session.sandbox_id,
                )
                self.store.upsert(existing_session)
                return sandbox, existing_session
            except Exception:
                self.store.delete(thread_id)
                self._handles.pop(thread_id, None)

        # No user identity, or no workspace PVC configured: fall back to the
        # shared template — old ephemeral, non-persistent behavior.
        template = self.config.template
        per_user_template = (
            user_id is not None and self.config.workspace_pvc_claim is not None
        )
        if per_user_template:
            template = self._ensure_user_template(str(user_id))

        # Warm pools and per-user isolation are mutually exclusive: a pooled pod
        # is pre-created and generic, so it cannot carry this user's
        # `subPath: users/{uid}` mount, and a pod's spec is immutable after
        # creation. Isolation wins — take the cold-start cost instead.
        warmpool = None if per_user_template else self.config.warmpool

        sandbox = self.client.create_sandbox(
            template=template,
            namespace=self.config.namespace,
            sandbox_ready_timeout=self.config.sandbox_ready_timeout,
            labels=self._labels_for_thread(thread_id),
            warmpool=warmpool,
            shutdown_after_seconds=self.config.shutdown_after_seconds,
        )
        session = SandboxSession(
            thread_id=thread_id,
            claim_name=sandbox.claim_name,
            sandbox_id=getattr(sandbox, "sandbox_id", None),
            namespace=self.config.namespace,
            template=template,
            user_id=user_id,
        )
        self._handles[thread_id] = sandbox
        self.store.upsert(session)
        return sandbox, session

    def _ensure_user_template(self, user_id: str) -> str:
        """Return a per-user SandboxTemplate name, creating it on first use.

        Clones the base ``self.config.template`` spec and adds a volume for
        ``self.config.workspace_pvc_claim`` mounted at
        ``self.config.workspace_mount_path`` with ``subPath: users/{user_id}``
        — that subPath is what makes one user's sandbox physically unable to
        read another user's files on the shared PVC.
        """
        name = f"python-sandbox-{hashlib.sha256(user_id.encode()).hexdigest()[:20]}"
        if name in self._user_template_names:
            return name

        from kubernetes.client.rest import ApiException

        from .k8s_helper import get_custom_objects_api

        api = get_custom_objects_api()
        group, version, plural = (
            "extensions.agents.x-k8s.io",
            "v1alpha1",
            "sandboxtemplates",
        )

        try:
            # get_namespaced_custom_object's overloaded signature (async_req)
            # makes pyright infer a union that includes non-dict overloads;
            # the sync call (default async_req=False) always returns a dict.
            base = cast(
                "dict[str, Any]",
                api.get_namespaced_custom_object(
                    group, version, self.config.namespace, plural, self.config.template
                ),
            )
        except ApiException as exc:
            raise RuntimeError(
                f"Base SandboxTemplate {self.config.template!r} not found in "
                f"namespace {self.config.namespace!r}: {exc}"
            ) from exc

        pod_spec = base["spec"]["podTemplate"]["spec"]
        containers = pod_spec.setdefault("containers", [])
        if not containers:
            raise RuntimeError(
                f"Base SandboxTemplate {self.config.template!r} has no containers "
                "to attach the workspace volume to."
            )

        volume_name = "user-workspace"
        pod_spec["volumes"] = [
            v for v in pod_spec.get("volumes", []) if v.get("name") != volume_name
        ] + [
            {
                "name": volume_name,
                "persistentVolumeClaim": {"claimName": self.config.workspace_pvc_claim},
            }
        ]
        for container in containers:
            container["volumeMounts"] = [
                m
                for m in container.get("volumeMounts", [])
                if m.get("name") != volume_name
            ] + [
                {
                    "name": volume_name,
                    "mountPath": self.config.workspace_mount_path,
                    "subPath": f"users/{user_id}",
                }
            ]
            container["env"] = [
                e
                for e in container.get("env", [])
                if e.get("name") != "CODE_INTERPRETER_WORKSPACE"
            ] + [
                {
                    "name": "CODE_INTERPRETER_WORKSPACE",
                    "value": self.config.workspace_mount_path,
                }
            ]
        pod_spec.setdefault("securityContext", {"runAsNonRoot": True})
        # Route the pod through gVisor (runsc) when configured, so the sandbox's
        # syscalls hit a user-space kernel instead of the host's.
        if self.config.runtime_class_name:
            pod_spec["runtimeClassName"] = self.config.runtime_class_name

        body = {
            "apiVersion": f"{group}/{version}",
            "kind": "SandboxTemplate",
            "metadata": {"name": name, "namespace": self.config.namespace},
            "spec": {**base["spec"], "podTemplate": {"spec": pod_spec}},
        }
        try:
            api.create_namespaced_custom_object(
                group, version, self.config.namespace, plural, body
            )
        except ApiException as exc:
            if exc.status != 409:  # already exists — fine, another request won the race
                raise RuntimeError(
                    f"Failed to create per-user SandboxTemplate {name!r}: {exc}"
                ) from exc
        self._user_template_names.add(name)
        return name

    def _runtime_json(
        self,
        sandbox: Any,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = sandbox.connector.send_request(method, endpoint, **kwargs)
        except SandboxRequestError as exc:
            detail = self._request_error_detail(exc)
            raise RuntimeError(detail) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Sandbox runtime returned non-JSON response from {endpoint}: "
                f"{response.text}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Sandbox runtime returned invalid payload: {data!r}")
        return data

    @staticmethod
    def _request_error_detail(exc: SandboxRequestError) -> str:
        response = getattr(exc, "response", None)
        if response is None:
            return str(exc)
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return (
            f"Sandbox runtime request failed"
            f"{f' with HTTP {exc.status_code}' if exc.status_code else ''}: "
            f"{payload}"
        )

    def _lock_for_thread(self, thread_id: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(thread_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[thread_id] = lock
            return lock

    @staticmethod
    def _is_handle_active(handle: Any | None) -> bool:
        return bool(handle is not None and getattr(handle, "is_active", True))

    @staticmethod
    def _labels_for_thread(thread_id: str) -> dict[str, str]:
        digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:20]
        return {
            "app": "code-interpreter",
            "ci-thread": f"t-{digest}",
        }

    @staticmethod
    def _with_session(
        action: str,
        thread_id: str,
        session: SandboxSession,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(data)
        payload.setdefault("status", "ok")
        payload["action"] = action
        payload["thread_id"] = thread_id
        payload["session"] = {
            "claim_name": session.claim_name,
            "sandbox_id": session.sandbox_id,
            "namespace": session.namespace,
            "template": session.template,
        }
        return payload

    @staticmethod
    def _add_data_uri(file_data: dict[str, Any]) -> None:
        content_base64 = file_data.get("content_base64")
        if not isinstance(content_base64, str) or not content_base64:
            return
        mime_type = file_data.get("mime_type")
        if not isinstance(mime_type, str):
            mime_type, _ = mimetypes.guess_type(str(file_data.get("path", "")))
        file_data["data_uri"] = (
            f"data:{mime_type or 'application/octet-stream'};base64,{content_base64}"
        )


def encode_text_file(content: str) -> str:
    return base64.b64encode(content.encode("utf-8")).decode("ascii")
