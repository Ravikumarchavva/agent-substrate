"""SandboxRuntime — the contract every code-execution backend implements.

The seam that decouples *what* the agent asks for (run this code, in this
session's directory) from *how* it is isolated (bubblewrap namespaces on a
single host, a gVisor pod in Kubernetes, or nothing at all in tests). Adding a
Firecracker/Kata/Docker backend later means adding one module here — no agent,
tool, or serving code changes.

Isolation is the runtime's job, not the caller's. ``SandboxSpec`` states the
*intent* (this user, this session, this directory, these limits, this network
policy); each runtime enforces it with whatever primitive it has.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


def is_display_artifact(mime_type: str) -> bool:
    """Whether a generated file should render inline in chat rather than just
    being offered for download. Mirrors ``sandbox_runtime._is_display_artifact``
    (that copy runs inside the sandbox image; this one runs host-side)."""
    return mime_type.startswith("image/") or mime_type in {
        "text/html",
        "application/pdf",
        "image/svg+xml",
    }


class NetworkPolicy(str, enum.Enum):
    """What the executed code may reach on the network.

    ``DENY`` is the default because sandboxed code is LLM-generated and
    untrusted: with no network it cannot exfiltrate the user's files even if it
    reads them. ``PIP_ONLY`` still denies the *user's* code any network — the
    package install runs as a separate, earlier execution in its own sandbox
    that has network but no view of user data (``pip install`` runs arbitrary
    ``setup.py`` code, so it must not see the workspace).
    """

    DENY = "deny"
    PIP_ONLY = "pip_only"
    FULL = "full"


@dataclass(slots=True)
class SandboxSpec:
    """One execution request.

    Exactly one of ``code`` (Python source) or ``argv`` (a shell command, already
    split) is set — the tool enforces that. ``session_dir`` is the *store-relative*
    key (``users/{uid}/sessions/{tid}``); runtimes resolve it against their own
    root, so a runtime is never handed a host path it must trust.
    """

    user_id: str | None
    thread_id: str
    session_dir: str
    code: str | None = None
    argv: list[str] | None = None
    timeout_s: int = 60
    network: NetworkPolicy = NetworkPolicy.DENY
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    # Extra runtime-specific hints (e.g. a venv path for PIP_ONLY). Kept opaque
    # so backends can differ without widening this dataclass.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecResult:
    """Outcome of one execution.

    ``output_files`` uses the same entry shape ``sandbox_response`` already
    expects (``name``/``mime_type``/``content_base64``/…), so the existing
    ``sandbox_result_to_tool_result`` conversion is reused unchanged.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    output_files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_sandbox_response(self) -> dict[str, Any]:
        """Shape this like sandbox_runtime.py's ``/ci/run`` body so the shared
        ``sandbox_result_to_tool_result`` converter works for every runtime."""
        return {
            "status": "ok" if self.ok else "error",
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "output_files": self.output_files,
            "artifacts": [
                item
                for item in self.output_files
                if is_display_artifact(str(item.get("mime_type", "")))
            ],
        }


class SandboxUnavailableError(RuntimeError):
    """Raised at startup/preflight when a runtime cannot work on this host —
    e.g. ``bwrap`` missing, or unprivileged user namespaces disabled. Surfaced
    loudly rather than silently degrading to an unisolated fallback."""


@runtime_checkable
class SandboxRuntime(Protocol):
    """A code-execution backend.

    Implementations must guarantee that an execution can only read and write
    within its own ``spec.session_dir`` — enforced by the kernel (mount
    namespace, pod ``subPath``, …), never by convention alone.
    """

    name: str

    async def execute(self, spec: SandboxSpec) -> ExecResult: ...

    async def stop(self) -> None:
        """Release any backend resources (pods, processes, caches)."""


__all__ = [
    "ExecResult",
    "NetworkPolicy",
    "SandboxRuntime",
    "SandboxSpec",
    "SandboxUnavailableError",
    "is_display_artifact",
]
