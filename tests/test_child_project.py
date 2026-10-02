"""The acceptance project (``examples/child-project``) runs against the current source on every build.

``make test-child`` is the isolated version — a fresh environment holding only the engine — which is what proves a
downstream project needs nothing that is not in the package. This runs the same tests here so they cannot drift.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CHILD = Path(__file__).resolve().parents[1] / "examples" / "child-project"


def test_the_child_project_runs_on_the_library_alone(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(CHILD / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(CHILD), "-c", str(CHILD / "pyproject.toml")],
        cwd=CHILD,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]
    assert "passed" in result.stdout
