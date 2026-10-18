"""The application attaches to compute; it never contains an engine or launches host processes."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import substrate_data

SOURCE = Path(substrate_data.__file__).parent
# The worker's own isolated batch child is the compute side of the boundary, not the application.
WORKER_ONLY = {"compute/service.py"}
FORBIDDEN_MODULES = {"subprocess", "multiprocessing", "pty", "pexpect"}
FORBIDDEN_CALLS = {"system", "popen", "fork", "forkpty", "execv", "execve", "execvp", "posix_spawn"}


def python_files():
    for path in sorted(SOURCE.rglob("*.py")):
        yield path, path.relative_to(SOURCE).as_posix()


def test_application_source_cannot_launch_host_processes():
    offenders = []
    for path, name in python_files():
        if name in WORKER_ONLY:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                modules = {(node.module or "").split(".")[0]}
            else:
                modules = set()
            if modules & FORBIDDEN_MODULES:
                offenders.append(f"{name}:{node.lineno} imports {sorted(modules)}")
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and (node.attr in FORBIDDEN_CALLS or node.attr.startswith(("spawn", "exec")))
            ):
                offenders.append(f"{name}:{node.lineno} calls os.{node.attr}")
    assert offenders == []


def test_provisioning_modules_are_gone():
    assert not (SOURCE / "compute" / "providers").exists()


def test_control_plane_does_not_load_a_compute_engine():
    """The API and controller plan, authorize and route; they never load a query engine."""
    code = (
        "import sys\n"
        "import substrate_data.api.app, substrate_data.compute.control_api\n"
        "loaded = {'duckdb', 'polars', 'substrate_data.compute.service',"
        " 'substrate_data.compute.batch'} & set(sys.modules)\n"
        "assert not loaded, sorted(loaded)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_settings_have_no_provisioning_or_embedded_modes():
    from substrate_data.core.models import Settings

    fields = set(Settings.model_fields)
    assert not {n for n in fields if "docker" in n or "worker_image" in n or "backend" in n}
    assert "compute_max_instances" not in fields
