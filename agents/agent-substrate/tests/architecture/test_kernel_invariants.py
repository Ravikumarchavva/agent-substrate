"""Architecture invariants that keep the kernel frozen.

These checks supplement the ``import-linter`` contracts in ``pyproject.toml``
(``agent stack layers`` + ``kernel is independent``) with cheap heuristics that
catch regressions early:

* No upward imports — the kernel (L0) must not import any layer above it
  (``fabric``, ``reasoning``, ``orchestration``, ``guardrails``, ``platform``)
  nor the orthogonal app modules (``integrations``, ``catalog``, ``server``, …).
* LOC and file-count ceilings — large new feature additions should not land
  in the kernel.
* No concrete agent / guardrail / middleware classes — only base ABCs,
  Protocols, and pure value types.

The kernel holds *all* contracts: ABCs, Protocols, dataclasses, enums (plus the
generic middleware-pipeline runner). Concrete implementations live in the
layers above. The ceilings carry ~20% headroom over the current size so small
contract refinements don't trip CI, while any substantial concrete addition
fails the build and forces the "which layer does this belong in?" conversation.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = REPO_ROOT / "src" / "ravi" / "kernel"

# Loose ceilings — current values are ~11.8k LOC and 95 files. The kernel holds
# only contracts (Protocols, ABCs, dataclasses, enums) + the generic middleware
# pipeline; concrete runtime/agents/policies live in fabric/reasoning/guardrails/
# platform. ~20% headroom flags unintended feature drift without nagging on
# small contract refinements.
MAX_KERNEL_LOC = 14_000
MAX_KERNEL_FILES = 115


def _iter_kernel_files() -> list[Path]:
    return [p for p in KERNEL_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_kernel_loc_ceiling() -> None:
    files = _iter_kernel_files()
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)
    assert total < MAX_KERNEL_LOC, (
        f"Kernel size grew to {total} LOC (ceiling {MAX_KERNEL_LOC}). "
        f"Concrete code belongs in fabric/reasoning/orchestration/guardrails/"
        f"platform, not ravi/kernel/ — the kernel holds contracts only."
    )


def test_kernel_file_count_ceiling() -> None:
    n = len(_iter_kernel_files())
    assert n < MAX_KERNEL_FILES, (
        f"Kernel grew to {n} files (ceiling {MAX_KERNEL_FILES}). "
        f"Have you added a feature module here that belongs in a layer above?"
    )


# Upward-import patterns that must never appear in kernel source: the five
# stack layers above L0, plus the orthogonal application modules.
_FORBIDDEN_PREFIXES = (
    "ravi.fabric",
    "ravi.reasoning",
    "ravi.orchestration",
    "ravi.guardrails",
    "ravi.platform",
    "ravi.integrations",
    "ravi.catalog",
    "ravi.server",
    "ravi.services",
    "ravi.shared",
    "ravi.configs",
    "ravi.logger",
)


def test_kernel_has_no_upward_imports() -> None:
    """No file in kernel may import from any layer above it."""
    violations: list[str] = []
    for path in _iter_kernel_files():
        text = path.read_text(encoding="utf-8")
        # Strip docstrings (triple-quoted blocks) before matching — docstring
        # examples that grep would flag are not real imports.
        stripped = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        for prefix in _FORBIDDEN_PREFIXES:
            for match in re.finditer(
                rf"^\s*(?:from\s+{re.escape(prefix)}|import\s+{re.escape(prefix)})",
                stripped,
                re.MULTILINE,
            ):
                # Report file:position
                relpath = path.relative_to(REPO_ROOT)
                violations.append(f"{relpath}: imports {prefix} ({match.group(0).strip()})")
    assert not violations, (
        "Kernel must not import from layers above it. Violations:\n  "
        + "\n  ".join(violations)
    )


def test_kernel_agents_contains_only_base() -> None:
    """``kernel/agents/`` may define only the agent contract and result types."""
    agents_dir = KERNEL_DIR / "agents"
    allowed_classes = {
        "AgentProtocol",    # Protocol — structural agent contract
        "AgentConfig",      # dataclass / model
        "AgentRunResult",
        "AggregatedUsage",
        "StepResult",
        "ToolCallRecord",
        "RunStatus",        # enum
    }
    found: list[str] = []
    for path in agents_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class\s+(\w+)\s*[\(:]", text, re.MULTILINE):
            name = match.group(1)
            if name not in allowed_classes:
                relpath = path.relative_to(REPO_ROOT)
                found.append(f"{relpath}: defines {name}")
    assert not found, (
        "Concrete agents must live in ravi/reasoning/agents/ or "
        "ravi/orchestration/agents/, not kernel/. The ActorAgent base lives in "
        "ravi/fabric/actors/. Found in kernel:\n  " + "\n  ".join(found)
    )


def test_kernel_guardrails_contains_only_base() -> None:
    """``kernel/guardrails/`` may define only the ABC and result types."""
    guardrails_dir = KERNEL_DIR / "guardrails"
    allowed_classes = {
        "BaseGuardrail",  # ABC
        "GuardrailContext",  # dataclass
        "GuardrailResult",  # dataclass
        "GuardrailType",  # enum
    }
    found: list[str] = []
    for path in guardrails_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class\s+(\w+)\s*[\(:]", text, re.MULTILINE):
            name = match.group(1)
            if name not in allowed_classes:
                relpath = path.relative_to(REPO_ROOT)
                found.append(f"{relpath}: defines {name}")
    assert not found, (
        "Concrete guardrails must live in ravi/reasoning/guardrails/. "
        "Found in kernel:\n  " + "\n  ".join(found)
    )


def test_kernel_middleware_contains_only_base() -> None:
    """``kernel/middleware/`` may define only the ABC and the pipeline runner."""
    middleware_dir = KERNEL_DIR / "middleware"
    allowed_classes = {
        "BaseMiddleware",  # ABC
        "MiddlewareContext",  # dataclass
        "MiddlewareStage",  # enum
        "MiddlewarePipeline",  # generic orchestrator (pure infrastructure)
    }
    found: list[str] = []
    for path in middleware_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class\s+(\w+)\s*[\(:]", text, re.MULTILINE):
            name = match.group(1)
            if name not in allowed_classes:
                relpath = path.relative_to(REPO_ROOT)
                found.append(f"{relpath}: defines {name}")
    assert not found, (
        "Concrete middleware must live in ravi/reasoning/middleware/. "
        "Found in kernel:\n  " + "\n  ".join(found)
    )
