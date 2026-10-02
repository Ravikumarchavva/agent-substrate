"""The examples are run, not just read: each one executes end to end offline, so they cannot drift from the API.

They use a scripted model (``examples/_model.py``) instead of a real provider, and keep their state in the folder the
test gives them — never the repository's.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

# example -> what its output must contain
EXPECTED = {
    "01_quickstart.py": "The capital of France is Paris.",
    "02_tools.py": "The phrase has 6 words, and 6 * 7 is 42.",
    "03_conversations.py": "after restart: I remember: My name is Ada. | What did I tell you?",
    "04_human_approval.py": "journaled: {'decision': 'approved', 'decided_by': 'alice'",
    "05_multi_agent.py": "All 3 records are valid.",
    "06_guardrails.py": "tool calls seen by our middleware: 1",
    "07_mcp_tools.py": "19 + 23 = 42.",
    "08_serve_http.py": '"type": "run.completed"',
    "09_evals.py": "Passed:    2 (66.7%)",
}


def test_every_example_is_covered() -> None:
    present = {p.name for p in EXAMPLES.glob("*.py") if not p.name.startswith("_")}
    assert present == set(EXPECTED), f"examples without a check, or checks without an example: {present ^ set(EXPECTED)}"


@pytest.mark.parametrize("example", sorted(EXPECTED))
def test_example_runs_offline(example: str, tmp_path: Path) -> None:
    env = {**os.environ, "SUBSTRATE_EXAMPLES_OFFLINE": "1", "PYTHONPATH": str(EXAMPLES), "OPENAI_API_KEY": ""}
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / example)],
        cwd=tmp_path,  # ``./.substrate`` lands here
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert EXPECTED[example] in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
