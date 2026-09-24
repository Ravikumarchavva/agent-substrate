"""NsjailRuntime — kernel-isolated execution via Google's ``nsjail``.

The security model: containers are not the security primitive — Linux
**namespaces** are, and Docker is a daemon that calls
``clone(CLONE_NEWNS|CLONE_NEWUSER|…)`` on your behalf. ``nsjail`` makes the
same syscalls directly, giving an identical kernel-enforced boundary with
**no daemon, no Docker socket, no setuid binary, no root, and no nested
virtualization**. On top of that it wires up a per-execution **cgroup**
natively (pids/memory), so process-count and memory caps actually scope to
just that one sandbox — not achievable with rlimits alone, since
``RLIMIT_NPROC`` caps processes per-*UID system-wide*, not per-sandbox.

Only ``spec.session_dir`` is bind-mounted into the sandbox, at
``/workspace`` (read-write); every other user's directory is *absent from
the mount namespace*, not merely permission-denied, so a traversal like
``../../other_user`` resolves to nothing. Each execution is a fresh
process: no interpreter state survives between turns.

Verified in a real, non-simulated spike (built nsjail from source pinned
to release ``3.6``, both inside a privileged Docker container with
``--cgroupns=host`` and directly on a bare Ubuntu 26.04 host — not assumed
from documentation):

* Execution, namespace isolation (host files absent from the jail), and
  timeout enforcement (``-t``, SIGKILL) all work.
* The process-count cap (``--cgroup_pids_max``) genuinely works: a
  fork-bomb hit ``Cannot fork`` at the configured cap — a real per-sandbox
  cgroup, not just an rlimit (``RLIMIT_NPROC`` caps processes per-*UID
  system-wide*, which is not what a per-sandbox cap needs).
* The memory cap (``--cgroup_mem_max``) **works**, but needs
  ``--cgroup_mem_swap_max=0`` alongside it. Without a swap cap, a process
  over the memory limit is pushed into swap instead of OOM-killed — that
  looked like "memory limits don't work" in an earlier pass of this spike,
  but was a missing flag, not a real nsjail limitation. Confirmed:
  ``memory.max`` alone let a 1000MB allocation succeed silently;
  adding ``memory.swap.max=0`` made the same allocation SIGKILL
  (exit 137).
* An unprivileged (non-root) process **cannot** create a cgroup at
  nsjail's default mount (``/sys/fs/cgroup`` — the cgroup root, root-owned
  everywhere). It must instead point ``--cgroupv2_mount`` at whatever
  cgroup v2 subtree systemd has already delegated to it — normally
  ``/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service``, which
  systemd (>= 245, i.e. any current distro) delegates ``cpu``/``memory``/
  ``pids`` to by default. This runtime resolves that path itself (see
  ``_cgroupv2_mount_for_this_process``) — no manual host setup was needed
  on this test host once the correct mount was passed. A root process
  (typical inside a container) keeps using the default root mount, which
  it already owns outright.
* ``--chroot`` must point at a real, writable, **non-tmpfs** directory —
  not ``/`` and not anywhere under ``/tmp`` or ``/run``. nsjail bind-mounts
  the chroot dir onto its own staging root and then remounts it read-only;
  any ``-R``/``-B`` target that doesn't already exist there gets created
  with ``mkdir``, which needs write access nsjail doesn't have at the real
  host ``/``. Separately, ``/tmp``/``/run/user/<uid>`` are tmpfs mounted
  ``nosuid,nodev`` by systemd, and the kernel refuses an unprivileged
  remount that can't preserve those "locked" flags — confirmed by
  reproducing the exact ``mount(..., MS_REMOUNT|MS_BIND|MS_RDONLY):
  Operation not permitted`` failure switching between a ``/tmp``-backed and
  an ext4-backed chroot dir with an otherwise identical command. This
  runtime chroots into a stable directory under ``/var/tmp`` (POSIX
  convention: persistent, not tmpfs, present on effectively every Linux
  host) rather than the workspace root, since the workspace root itself
  may be caller-configured onto tmpfs (e.g. pytest's ``tmp_path``).
* nsjail has no bwrap-style synthetic ``--dev``: without ``/dev/urandom``
  (and ``/dev/null``/``zero``/``full``/``random``) bound in explicitly,
  CPython itself fails at startup (``_Py_HashRandomization_Init``), before
  any user code runs. Fixed by binding ``_RO_DEV_NODES``.
* nsjail ``execve()``s ``argv[0]`` directly — no ``$PATH`` search, unlike
  bwrap. A bare command name (e.g. ``spec.argv == ["ls", "-1"]``) must be
  resolved to an absolute path before being handed to nsjail; this runtime
  does that resolution against the host (safe, since the jail mirrors the
  same absolute paths under ``_RO_HOST_PATHS``).
* Seccomp-bpf filtering (via nsjail's bundled Kafel policy language)
  mechanically works — a disallowed syscall is genuinely killed (SIGSYS) —
  but a *correct* allowlist for a general-purpose Python interpreter is
  real, ongoing curation work (a naive list starves the interpreter's own
  startup), not a one-time drop-in. Deliberately **not enabled by default**
  here; ``seccomp_policy_path`` exists for when that policy is built and
  tested against this deployment's actual workloads.

All seven of this module's own tests (``tests/integrations/
test_sandbox_runtimes.py``, ``-k nsjail``) pass for real against this exact
built binary on a bare, unprivileged Ubuntu host — none of them skip.

Requires the ``nsjail`` binary on ``PATH`` (build from
github.com/google/nsjail — it is not commonly packaged, no apt/Debian
package or prebuilt release binary exists). If this process runs as root
inside a container, that container needs ``--cgroupns=host`` (or
equivalent cgroup delegation) for the cgroup root to be writable at all.
If it runs unprivileged on a bare host, it needs a systemd-delegated
cgroup subtree (see above) — ``preflight()`` checks for both and raises
with the specific remediation for whichever is missing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from substrate.logger import setup_logging

from ._files import collect_changed, snapshot
from .base import ExecResult, NetworkPolicy, SandboxSpec, SandboxUnavailableError

# Read-only host libraries and font configuration needed by runtime interpreters
_RO_HOST_PATHS = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc/ssl",
    "/etc/alternatives",
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

logger = setup_logging()

# Max processes per sandbox (cgroup pids.max)
_DEFAULT_MAX_PIDS = 64

_CGROUPV2_ROOT = Path("/sys/fs/cgroup")

# Base directory for nsjail private mount namespace chroot
_CHROOT_BASE = Path("/var/tmp/substrate-nsjail-chroot")

# Essential character devices required for interpreter startup (e.g. hash randomization)
_RO_DEV_NODES = (
    "/dev/null",
    "/dev/zero",
    "/dev/full",
    "/dev/random",
    "/dev/urandom",
)


def _cgroupv2_mount_for_this_process() -> str | None:
    """Where this process can actually create an nsjail cgroup.

    Root owns the cgroup root outright (typical inside a container with
    ``--cgroupns=host``). An unprivileged user owns nothing there — only
    whatever systemd has delegated to it, normally its own user-manager
    slice. Returns ``None`` if neither is usable, so callers can fail
    closed with an actionable message instead of nsjail's raw ENOENT/EPERM.
    """
    if os.geteuid() == 0:
        return str(_CGROUPV2_ROOT)
    delegated = (
        _CGROUPV2_ROOT
        / "user.slice"
        / f"user-{os.getuid()}.slice"
        / f"user@{os.getuid()}.service"
    )
    subtree_control = delegated / "cgroup.subtree_control"
    try:
        controllers = subtree_control.read_text().split()
    except OSError:
        return None
    if "pids" in controllers:
        return str(delegated)
    return None


class NsjailRuntime:
    """Execute code in an nsjail namespace scoped to one session directory."""

    name = "nsjail"

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        python_bin: str = "",
        nsjail_bin: str = "nsjail",
        max_pids: int = _DEFAULT_MAX_PIDS,
        seccomp_policy_path: str | None = None,
    ) -> None:
        self._root = Path(workspace_root).resolve()
        self._python_bin = python_bin or sys.executable
        # Resolve to an absolute path now: the child process runs under
        # ``_sandbox_env()``'s minimal PATH (deliberately stripped of the
        # host's own PATH, which may include user-local install dirs like
        # ``~/.local/bin`` where nsjail commonly lives when built from
        # source), so a bare binary name would silently fail to launch.
        self._nsjail_bin = shutil.which(nsjail_bin) or nsjail_bin
        self._max_pids = max_pids
        self._seccomp_policy_path = seccomp_policy_path
        self._python_prefixes = _interpreter_prefixes(self._python_bin)
        self._cgroupv2_mount = _cgroupv2_mount_for_this_process()

    # ── preflight ────────────────────────────────────────────────────────────
    def preflight(self) -> None:
        """Fail loudly at startup if this host cannot isolate — a silent
        downgrade here would mean running untrusted code with no boundary."""
        if shutil.which(self._nsjail_bin) is None:
            raise SandboxUnavailableError(
                f"{self._nsjail_bin!r} not found on PATH. Build it from "
                "github.com/google/nsjail (not commonly packaged), or set "
                "SANDBOX_RUNTIME to another backend."
            )
        if self._cgroupv2_mount is None:
            raise SandboxUnavailableError(
                "No usable cgroup v2 subtree for this process. Running as "
                "root: nsjail needs the cgroup root (typically inside a "
                "container started with --cgroupns=host). Running "
                "unprivileged: nsjail needs systemd's delegated user slice "
                f"at /sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/"
                f"user@{os.getuid()}.service, with 'pids' present in its "
                "cgroup.subtree_control — normal on any systemd >= 245 host; "
                "if missing, run `systemctl edit user@$(id -u).service` and "
                "add `[Service]\\nDelegate=cpu memory pids`, then re-login."
            )
        _CHROOT_BASE.mkdir(parents=True, exist_ok=True)
        probe_argv = [
            self._nsjail_bin,
            "-Mo",
            "-q",
            "--use_cgroupv2",
            "--cgroupv2_mount",
            self._cgroupv2_mount,
            f"--cgroup_pids_max={self._max_pids}",
            "--chroot",
            str(_CHROOT_BASE),
        ]
        for host_path in _RO_HOST_PATHS:
            if Path(host_path).exists():
                probe_argv += ["-R", host_path]
        probe_argv += ["--", "/bin/sh", "-c", "exit 0"]
        probe = subprocess.run(  # noqa: S603 - fixed argv, no user input
            probe_argv, capture_output=True, text=True, timeout=15
        )
        if probe.returncode != 0:
            hint = ""
            stderr = (probe.stderr or "").lower()
            if "cgroupns=host" in stderr or "couldn't setup parent cgroup" in stderr:
                hint = (
                    " If this process runs inside a container, it needs "
                    "--cgroupns=host (or equivalent host cgroup delegation) "
                    "for nsjail's cgroup limits to initialize."
                )
            raise SandboxUnavailableError(
                f"nsjail cannot create a sandbox on this host: "
                f"{(probe.stderr or '').strip()}{hint}"
            )
        logger.info(
            "nsjail preflight OK — cgroup v2 mount %s", self._cgroupv2_mount
        )

    # ── execution ────────────────────────────────────────────────────────────
    async def execute(self, spec: SandboxSpec) -> ExecResult:
        session_path = self._resolve_session(spec.session_dir)
        session_path.mkdir(parents=True, exist_ok=True)

        if spec.code is not None:
            argv = [self._python_bin, "-c", f"{_PY_PREAMBLE}\n{spec.code}"]
        elif spec.argv:
            # nsjail execve()s argv[0] directly with no PATH search (unlike
            # bwrap, which resolves bare command names itself) — resolve
            # against the host now, since the jail mirrors the same
            # absolute paths for everything under _RO_HOST_PATHS.
            argv = list(spec.argv)
            if argv and not Path(argv[0]).is_absolute():
                argv[0] = shutil.which(argv[0]) or argv[0]
        else:
            return ExecResult(stderr="No code or command supplied.", exit_code=2)

        before = snapshot(session_path)
        cmd = self._nsjail_argv(spec) + argv

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_sandbox_env(),
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailableError(f"Cannot start sandbox: {exc}") from exc

        try:
            raw_out, raw_err = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_s + 5
            )
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            # Backstop only: nsjail's own `-t` below should already have
            # killed the child well before this fires.
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
        """Nothing to release: every execution is a fresh process, and
        nsjail is PID 1 of its own PID namespace — killing it tears down
        everything inside."""

    # ── internals ────────────────────────────────────────────────────────────
    def _resolve_session(self, session_dir: str) -> Path:
        """Reject traversal — the same rule ``WorkspaceFileStore._resolve`` uses, so both sides of the mount agree."""
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

    def _nsjail_argv(self, spec: SandboxSpec) -> list[str]:
        session_path = self._resolve_session(spec.session_dir)
        _CHROOT_BASE.mkdir(parents=True, exist_ok=True)
        argv = [
            self._nsjail_bin,
            "-Mo",  # standalone, single execution
            "-q",  # quiet nsjail's own framework logs, not the child's output
            "--use_cgroupv2",
            "--cgroupv2_mount",
            self._cgroupv2_mount or "",
            f"--cgroup_pids_max={self._max_pids}",
            "-t",
            str(spec.timeout_s),
            "--chroot",
            str(_CHROOT_BASE),
            "--cwd",
            "/workspace",
        ]
        if spec.memory_bytes:
            argv += [
                f"--cgroup_mem_max={spec.memory_bytes}",
                "--cgroup_mem_swap_max=0",  # prevent swapping past memory limit
            ]
        if self._seccomp_policy_path:
            argv += ["--seccomp_policy", self._seccomp_policy_path]

        for host_path in _RO_HOST_PATHS:
            if Path(host_path).exists():
                argv += ["-R", host_path]
        for dev_node in _RO_DEV_NODES:
            if Path(dev_node).exists():
                argv += ["-R", dev_node]
        for prefix in self._python_prefixes:
            argv += ["-R", prefix]

        # Shared conversation workspace, read-write.
        argv += ["-B", f"{session_path}:/workspace"]

        private_dir = spec.extra.get("private_dir")
        if private_dir:
            private_path = self._resolve_session(str(private_dir))
            private_path.mkdir(parents=True, exist_ok=True)
            argv += ["-B", f"{private_path}:/workspace/private"]

        argv += ["-T", "/tmp", "-E", "HOME=/tmp"]
        for key, value in _sandbox_env().items():
            if key != "HOME":
                argv += ["-E", f"{key}={value}"]

        if spec.network is NetworkPolicy.PIP_ONLY:
            venv = spec.extra.get("venv_path")
            if venv:
                argv += ["-R", str(venv)]
        if spec.network is NetworkPolicy.FULL:
            argv.append("--disable_clone_newnet")
            for resolv in ("/etc/resolv.conf", "/etc/hosts"):
                if Path(resolv).exists():
                    argv += ["-R", resolv]
        # NetworkPolicy.DENY: CLONE_NEWNET is nsjail's default

        argv.append("--")
        return argv


__all__ = ["NsjailRuntime"]
