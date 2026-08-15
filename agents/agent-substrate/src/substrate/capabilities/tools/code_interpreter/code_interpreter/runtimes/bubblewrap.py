"""BubblewrapRuntime — kernel-isolated execution on a single Linux host.

The security model, and why it is this rather than a container-per-session:
containers are not the security primitive — Linux **namespaces** are, and Docker
is a daemon that calls ``clone(CLONE_NEWNS|CLONE_NEWUSER|…)`` on your behalf.
``bwrap`` makes the same syscalls directly, so we get an identical
kernel-enforced boundary with **no daemon, no Docker socket, no setuid binary,
no root, and no nested virtualization** (``/dev/kvm`` is not required, so this
works on any Linux VPS). This is the mechanism online judges (IOI's ``isolate``,
Judge0, CSES) have used to run strangers' code for three decades, and what
OpenAI's Codex CLI uses for the same job.

The isolation that matters here: only ``spec.session_dir`` is bind-mounted into
the sandbox, at ``/workspace`` (read-write). Every other user's directory is
*absent from the mount namespace* — not merely permission-denied — so a
traversal like ``../../other_user`` resolves to nothing. Writes land straight
on the durable host volume, so the session directory doubles as persistent
storage with no sync layer (``sandbox:`` refs and ``FileVersion`` versioning
keep working unchanged).

One deliberate, narrow exception to "only session_dir is mounted": if the
calling user has a ``users/{uid}/kb`` directory (their standing knowledge-base
content), it is additionally bind-mounted at ``/workspace/.kb`` — **read-only**
(``--ro-bind``, not ``--bind``), so the model can read it but never delete or
overwrite it. Same traversal-rejecting resolution as the writable mount; see
``_bwrap_argv()``.

Each execution is a **fresh process**: no interpreter state survives between
turns, which removes cross-user variable leakage by construction (the previous
in-process runner shared one module-global ``exec`` namespace across all users).
"""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from substrate.logger import setup_logging

from ._files import collect_changed, snapshot
from .base import (
    ExecResult,
    NetworkPolicy,
    SandboxSpec,
    SandboxUnavailableError,
)

logger = setup_logging()

# Read-only host paths the interpreter needs to run at all (libs, binaries).
# Everything else — notably other users' data and the rest of the host fs — is
# simply never mounted, so it does not exist inside the sandbox.
_RO_HOST_PATHS = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc/ssl",
    "/etc/alternatives",
    # matplotlib/PIL shell out to fontconfig; without these every plot emits
    # "Fontconfig error: Cannot load default config file" onto stderr, which
    # would surface as noise in the agent's tool output.
    "/etc/fonts",
)

# Inline preamble: force a non-interactive matplotlib backend and a writable
# config dir, since $HOME inside the sandbox is a tmpfs.
_PY_PREAMBLE = textwrap.dedent(
    """
    import os
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl")
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
    except Exception:
        pass
    """
).strip()


class BubblewrapRuntime:
    """Execute code in a bubblewrap namespace scoped to one session directory."""

    name = "bubblewrap"

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        python_bin: str = "",
        bwrap_bin: str = "bwrap",
    ) -> None:
        self._root = Path(workspace_root).resolve()
        # Default to the interpreter we are running under, so a deployment that
        # installs the data-science packages into the engine's own environment
        # works with no extra configuration. Point SANDBOX_PYTHON at a dedicated
        # sandbox venv to keep those packages out of the engine image.
        self._python_bin = python_bin or sys.executable
        self._bwrap_bin = bwrap_bin
        # The interpreter's own prefix must be mounted, or `import pandas` fails
        # inside the sandbox: a venv python lives outside /usr and its
        # site-packages would simply not exist in the mount namespace.
        self._python_prefixes = _interpreter_prefixes(self._python_bin)

    # ── preflight ────────────────────────────────────────────────────────────
    def preflight(self) -> None:
        """Fail loudly at startup if this host cannot isolate.

        Deliberately raises instead of degrading to an unisolated fallback — a
        silent downgrade here would mean running untrusted code with no
        boundary, which is exactly the bug this runtime exists to fix.
        """
        if shutil.which(self._bwrap_bin) is None:
            raise SandboxUnavailableError(
                f"{self._bwrap_bin!r} not found on PATH. Install bubblewrap "
                "(apt install bubblewrap) or set SANDBOX_RUNTIME to another backend."
            )
        probe_argv = [self._bwrap_bin, "--unshare-all", "--die-with-parent"]
        for host_path in _RO_HOST_PATHS:
            if os.path.exists(host_path):
                probe_argv += ["--ro-bind", host_path, host_path]
        for prefix in self._python_prefixes:
            probe_argv += ["--ro-bind", prefix, prefix]
        probe_argv += [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--setenv",
            "HOME",
            "/tmp",
            "--",
            self._python_bin,
            "-c",
            "import sys",
        ]
        probe = subprocess.run(  # noqa: S603 - fixed argv, no user input
            probe_argv, capture_output=True, text=True, timeout=15
        )
        if probe.returncode != 0:
            hint = ""
            if "apparmor" in (probe.stderr or "").lower() or _apparmor_restricted():
                hint = (
                    " Ubuntu 24.04+ restricts unprivileged user namespaces: set "
                    "kernel.apparmor_restrict_unprivileged_userns=0, or run inside a "
                    "container with cap_add=SYS_ADMIN and seccomp=unconfined."
                )
            raise SandboxUnavailableError(
                f"bubblewrap cannot create a namespace on this host: "
                f"{(probe.stderr or '').strip()}{hint}"
            )

    # ── execution ────────────────────────────────────────────────────────────
    async def execute(self, spec: SandboxSpec) -> ExecResult:
        session_path = self._resolve_session(spec.session_dir)
        session_path.mkdir(parents=True, exist_ok=True)

        if spec.code is not None:
            argv = [self._python_bin, "-c", f"{_PY_PREAMBLE}\n{spec.code}"]
        elif spec.argv:
            argv = list(spec.argv)
        else:
            return ExecResult(stderr="No code or command supplied.", exit_code=2)

        before = snapshot(session_path)
        cmd = self._bwrap_argv(spec) + argv

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session_path),
                env=_sandbox_env(),
                preexec_fn=_limit_setter(spec),  # noqa: PLC0321
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailableError(f"Cannot start sandbox: {exc}") from exc

        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s + 5
            )
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(
                stderr=f"Execution timed out after {spec.timeout_s}s.", exit_code=124
            )

        stdout = raw_out.decode("utf-8", errors="replace")
        stderr = raw_err.decode("utf-8", errors="replace")
        output_files = collect_changed(session_path, before)
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            output_files=output_files,
        )

    async def stop(self) -> None:
        """Nothing to release: every execution is a fresh process and
        ``--die-with-parent`` guarantees no orphans outlive us."""

    # ── internals ────────────────────────────────────────────────────────────
    def _resolve_session(self, session_dir: str) -> Path:
        """Resolve the store-relative session key under our root, rejecting
        traversal — the same rule as ``WorkspaceFileStore._resolve`` so both
        sides of the mount agree."""
        key = session_dir.strip("/")
        if not key or ".." in Path(key).parts:
            raise ValueError(f"Invalid session_dir: {session_dir!r}")
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise ValueError(
                f"session_dir escapes workspace root: {session_dir!r}"
            ) from None
        return candidate

    def _bwrap_argv(self, spec: SandboxSpec) -> list[str]:
        session_path = self._resolve_session(spec.session_dir)
        argv = [
            self._bwrap_bin,
            "--unshare-all",  # mount/pid/ipc/uts/cgroup/user + net (see below)
            "--die-with-parent",  # no orphan processes if we are killed
            "--new-session",  # detach from our controlling TTY (TIOCSTI safety)
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home",
            "--setenv",
            "HOME",
            "/tmp",
        ]
        for host_path in _RO_HOST_PATHS:
            if os.path.exists(host_path):
                argv += ["--ro-bind", host_path, host_path]
        # The interpreter and its site-packages (read-only): without these the
        # sandbox has a bare Python and every `import pandas` fails.
        for prefix in self._python_prefixes:
            argv += ["--ro-bind", prefix, prefix]
        # THE isolation boundary: only this session's directory is present.
        argv += ["--bind", str(session_path), "/workspace", "--chdir", "/workspace"]

        # A second, deliberate exception to that boundary: a user's own
        # standing knowledge-base content, read-only. Resolved through the
        # same traversal-rejecting _resolve_session() the writable mount
        # above uses, not a hand-rolled path join. Existence-checked in
        # Python (matching _RO_HOST_PATHS's pattern above) rather than
        # relying on a `--ro-bind-try`-style flag, so a user with no KB
        # content yet gets no extra mount and nothing new can break for the
        # common case. See chat_intents.py for the matching system-prompt
        # note telling the model this path is read-only.
        if spec.user_id:
            kb_path = self._resolve_session(f"users/{spec.user_id}/kb")
            if kb_path.is_dir():
                argv += ["--ro-bind", str(kb_path), "/workspace/.kb"]

        # PIP_ONLY mounts a prepared venv read-only; the user's code still gets
        # no network (the install ran earlier, in its own sandbox).
        venv = spec.extra.get("venv_path")
        if spec.network is NetworkPolicy.PIP_ONLY and venv:
            argv += ["--ro-bind", str(venv), "/opt/venv"]
        if spec.network is NetworkPolicy.FULL:
            argv += ["--share-net"]
            for resolv in ("/etc/resolv.conf", "/etc/hosts"):
                if os.path.exists(resolv):
                    argv += ["--ro-bind", resolv, resolv]
        argv.append("--")
        return argv


def _interpreter_prefixes(python_bin: str) -> tuple[str, ...]:
    """Host directories that must be mounted for *python_bin* to import anything.

    Covers ``sys.prefix``/``sys.base_prefix`` (the venv plus the base install it
    was created from) *and* every hop of the executable's symlink chain. That
    last part matters: a venv python is typically a symlink into a
    version-manager directory, and tools like ``uv`` point it at an unversioned
    alias dir which is itself a symlink. Fully resolving the path would collapse
    that alias away, and the sandbox would then break the chain with
    ``execvp: No such file or directory`` — so each hop is mounted as written.

    Paths already covered by ``_RO_HOST_PATHS`` are skipped to avoid
    double-binding.
    """
    candidates: list[str] = []
    try:
        probe = subprocess.run(  # noqa: S603 - argv is fixed, python_bin is config
            [
                python_bin,
                "-c",
                "import sys; print(sys.prefix); print(sys.base_prefix)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        candidates += [
            line.strip() for line in probe.stdout.splitlines() if line.strip()
        ]
    except (OSError, subprocess.SubprocessError):
        pass

    # Walk the symlink chain, collecting the prefix of every hop as written.
    current = Path(shutil.which(python_bin) or python_bin)
    for _ in range(16):  # bounded: never loop forever on a symlink cycle
        candidates.append(str(current.parent.parent))
        if not current.is_symlink():
            break
        target = Path(os.readlink(current))
        current = target if target.is_absolute() else (current.parent / target)

    prefixes: list[str] = []
    for path in candidates:
        if not path or not os.path.isdir(path):
            continue
        if any(path == ro or path.startswith(f"{ro}/") for ro in _RO_HOST_PATHS):
            continue  # already mounted
        if path not in prefixes:
            prefixes.append(path)
    return tuple(prefixes)


def _apparmor_restricted() -> bool:
    try:
        return (
            Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
            .read_text()
            .strip()
            == "1"
        )
    except OSError:
        return False


def _sandbox_env() -> dict[str, str]:
    """A deliberately minimal environment — the host's env may hold API keys,
    DB URLs and JWT secrets, none of which untrusted code should ever see."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _limit_setter(spec: SandboxSpec):
    """Return a ``preexec_fn`` applying rlimits in the child before exec.

    Set here rather than passed to bwrap because rlimits are inherited across
    ``exec`` and cannot be raised again by the sandboxed code once lowered.
    """

    def _apply() -> None:  # pragma: no cover - runs in the forked child
        # NB: deliberately no RLIMIT_NPROC. It caps processes per *UID
        # system-wide*, not per sandbox, so a low value makes bwrap's own
        # clone() fail with EAGAIN once the host user has many processes.
        # Fork-bomb containment belongs to cgroups (see module docstring).
        resource.setrlimit(resource.RLIMIT_CPU, (spec.timeout_s, spec.timeout_s))
        if spec.memory_bytes:
            resource.setrlimit(
                resource.RLIMIT_AS, (spec.memory_bytes, spec.memory_bytes)
            )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return _apply


__all__ = ["BubblewrapRuntime"]
