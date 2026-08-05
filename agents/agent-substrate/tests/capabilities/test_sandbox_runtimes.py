"""Isolation guarantees of the sandbox runtimes.

These are security regression tests, not feature tests. Before this suite the
shared sandbox let one user's generated code read every other user's files and
inherit their interpreter state; ``test_cross_user_file_is_invisible`` and
``test_no_state_bleeds_between_executions`` are the two that must never regress.

``BubblewrapRuntime`` tests skip automatically where the host cannot create
unprivileged user namespaces (macOS, hardened kernels, CI without
``SYS_ADMIN``), so the suite stays green while still being meaningful on Linux.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes import (
    BubblewrapRuntime,
    InProcessRuntime,
    NetworkPolicy,
    SandboxRuntime,
    SandboxSpec,
    SandboxUnavailableError,
)

ALICE_DIR = "users/alice/sessions/s1"
BOB_SECRET = "BOB PRIVATE DATA"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Two users' trees under one root — the layout the real store uses."""
    (tmp_path / ALICE_DIR).mkdir(parents=True)
    bob = tmp_path / "users/bob/sessions/s2"
    bob.mkdir(parents=True)
    (bob / "private.xlsx").write_text(BOB_SECRET)
    return tmp_path


def _bwrap_or_skip(root: Path) -> BubblewrapRuntime:
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap not installed on this host")
    runtime = BubblewrapRuntime(root)
    try:
        runtime.preflight()
    except SandboxUnavailableError as exc:
        pytest.skip(f"host cannot create user namespaces: {exc}")
    return runtime


def spec(**kwargs) -> SandboxSpec:
    kwargs.setdefault("timeout_s", 30)
    return SandboxSpec(user_id="alice", thread_id="s1", session_dir=ALICE_DIR, **kwargs)


# ── the two security regressions ─────────────────────────────────────────────


async def test_cross_user_file_is_invisible(workspace: Path) -> None:
    """Alice's code must not be able to read Bob's file by absolute path.

    Not merely permission-denied: the path must not exist in the sandbox's
    mount namespace at all.
    """
    runtime = _bwrap_or_skip(workspace)
    bob_file = workspace / "users/bob/sessions/s2/private.xlsx"
    result = await runtime.execute(
        spec(
            code=(
                "try:\n"
                f"    print('LEAK:' + open({str(bob_file)!r}).read())\n"
                "except Exception as exc:\n"
                "    print('blocked:' + type(exc).__name__)\n"
            )
        )
    )
    assert "LEAK" not in result.stdout, result.stdout
    assert "blocked:FileNotFoundError" in result.stdout
    # And Bob's file is untouched on the host.
    assert bob_file.read_text() == BOB_SECRET


async def test_traversal_out_of_session_is_blocked(workspace: Path) -> None:
    runtime = _bwrap_or_skip(workspace)
    result = await runtime.execute(
        spec(
            code=(
                "import os\n"
                "print('entries:', sorted(os.listdir('/workspace')))\n"
                "try:\n"
                "    os.listdir('/workspace/../bob')\n"
                "    print('LEAK')\n"
                "except Exception as exc:\n"
                "    print('blocked:' + type(exc).__name__)\n"
            )
        )
    )
    assert "LEAK" not in result.stdout
    assert "blocked:FileNotFoundError" in result.stdout


@pytest.mark.parametrize("runtime_name", ["bubblewrap", "inprocess"])
async def test_no_state_bleeds_between_executions(
    workspace: Path, runtime_name: str
) -> None:
    """A variable set by one execution must not exist in the next.

    Guards the old module-global ``_session_globals``, which shared one
    namespace across every user and session.
    """
    runtime: SandboxRuntime = (
        _bwrap_or_skip(workspace)
        if runtime_name == "bubblewrap"
        else InProcessRuntime(workspace)
    )
    await runtime.execute(spec(code="SECRET_TOKEN = 'alice-private'"))
    result = await runtime.execute(
        spec(
            code=(
                "try:\n"
                "    print('LEAK:' + SECRET_TOKEN)\n"
                "except NameError:\n"
                "    print('clean')\n"
            )
        )
    )
    assert "LEAK" not in result.stdout
    assert "clean" in result.stdout


# ── containment of the execution itself ──────────────────────────────────────


async def test_network_denied_by_default(workspace: Path) -> None:
    runtime = _bwrap_or_skip(workspace)
    result = await runtime.execute(
        spec(
            network=NetworkPolicy.DENY,
            code=(
                "import socket\n"
                "try:\n"
                "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
                "    print('LEAK')\n"
                "except Exception as exc:\n"
                "    print('blocked:' + type(exc).__name__)\n"
            ),
        )
    )
    assert "LEAK" not in result.stdout
    assert "blocked:" in result.stdout


async def test_host_environment_is_not_inherited(workspace: Path) -> None:
    """Secrets in the host env (API keys, JWT_SECRET, DATABASE_URL) must not
    reach code the model wrote."""
    runtime = _bwrap_or_skip(workspace)
    result = await runtime.execute(spec(code="import os; print(sorted(os.environ))"))
    for leaked in ("OPENAI_API_KEY", "JWT_SECRET", "DATABASE_URL", "AWS_SECRET"):
        assert leaked not in result.stdout


async def test_timeout_terminates_runaway_code(workspace: Path) -> None:
    runtime = _bwrap_or_skip(workspace)
    result = await runtime.execute(spec(code="while True: pass", timeout_s=3))
    assert not result.ok


async def test_rlimit_cannot_be_raised_by_sandboxed_code(workspace: Path) -> None:
    runtime = _bwrap_or_skip(workspace)
    result = await runtime.execute(
        spec(
            code=(
                "import resource\n"
                "try:\n"
                "    resource.setrlimit(resource.RLIMIT_AS,\n"
                "        (resource.RLIM_INFINITY, resource.RLIM_INFINITY))\n"
                "    print('LEAK')\n"
                "except Exception as exc:\n"
                "    print('blocked:' + type(exc).__name__)\n"
            )
        )
    )
    assert "LEAK" not in result.stdout
    assert "blocked:" in result.stdout


# ── the features that must still work inside the sandbox ─────────────────────


@pytest.mark.parametrize("runtime_name", ["bubblewrap", "inprocess"])
async def test_generated_file_persists_to_host(
    workspace: Path, runtime_name: str
) -> None:
    """The session dir doubles as durable storage: what the sandbox writes must
    land on the host volume, where the file-serve endpoint and versioning read."""
    runtime: SandboxRuntime = (
        _bwrap_or_skip(workspace)
        if runtime_name == "bubblewrap"
        else InProcessRuntime(workspace)
    )
    result = await runtime.execute(
        spec(code="open('report.csv', 'w').write('a,b\\n1,2\\n')")
    )
    assert result.ok, result.stderr
    assert (workspace / ALICE_DIR / "report.csv").read_text().startswith("a,b")
    assert "report.csv" in [f["name"] for f in result.output_files]


@pytest.mark.parametrize("runtime_name", ["bubblewrap", "inprocess"])
async def test_shell_command_path(workspace: Path, runtime_name: str) -> None:
    """The `command` tool parameter (ls, wc, ...) runs in the session dir."""
    runtime: SandboxRuntime = (
        _bwrap_or_skip(workspace)
        if runtime_name == "bubblewrap"
        else InProcessRuntime(workspace)
    )
    (workspace / ALICE_DIR / "data.txt").write_text("x\n")
    result = await runtime.execute(spec(argv=["ls", "-1"]))
    assert result.ok, result.stderr
    assert result.stdout.split() == ["data.txt"]


async def test_session_dir_traversal_is_rejected(workspace: Path) -> None:
    """A malformed session_dir must never resolve outside the workspace root."""
    runtime = BubblewrapRuntime(workspace)
    for bad in ("../../etc", "users/../../etc", ""):
        with pytest.raises(ValueError):
            await runtime.execute(
                SandboxSpec(
                    user_id="alice", thread_id="s1", session_dir=bad, code="pass"
                )
            )


async def test_missing_code_and_command_is_an_error(workspace: Path) -> None:
    runtime = InProcessRuntime(workspace)
    result = await runtime.execute(spec())
    assert not result.ok
    assert "No code or command" in result.stderr or result.exit_code != 0


# ── the unsafe runtime is honest about being unsafe ──────────────────────────


async def test_inprocess_runtime_does_not_isolate(workspace: Path) -> None:
    """Documents the known trade-off: InProcessRuntime is for tests only, and it
    can reach other users' files. If this ever starts passing as 'blocked',
    someone has added isolation and this test should become a real guarantee."""
    runtime = InProcessRuntime(workspace)
    bob_file = workspace / "users/bob/sessions/s2/private.xlsx"
    result = await runtime.execute(spec(code=f"print(open({str(bob_file)!r}).read())"))
    assert BOB_SECRET in result.stdout
