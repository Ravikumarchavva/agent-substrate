"""The shape of the core package, as the structure tests check it.

``CORE`` lists the concepts of ``substrate`` from the bottom up: a concept may import the ones before it, never
the ones after (``tests/invariants/test_structure.py`` and the import-linter ``layers`` contract).

``CONTRACTS`` are the modules someone implementing a port depends on — the protocols and the value types in
their signatures. They stay free of I/O, of the engine, and of any vendor (``tests/architecture/test_contracts.py``).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "substrate"

CORE = (
    "types",
    "telemetry",
    "tools",
    "models",
    "stores",
    "documents",
    "workspace",
    "safety",
    "context",
    "middleware",
    "runtime",
    "agents",
    "testing",
)

CONTRACTS = (
    "types",
    "tools.protocols",
    "tools.approval",
    "tools.chain",
    "tools.skills",
    "models.protocols",
    "stores.blob",
    "stores.files",
    "stores.graph",
    "stores.memory",
    "stores.tasks",
    "stores.threads",
    "stores.vector",
    "documents.protocols",
    "documents.types",
    "workspace.protocols",
    "safety.protocols",
    "context.protocols",
    "middleware.stage",
    "runtime.agent",
    "runtime.communication",
    "runtime.effects",
    "runtime.inbox",
    "runtime.message",
    "runtime.scheduler",
    "runtime.store",
    "runtime.supervisor",
)


def core_files(*, include_testing: bool = False) -> list[Path]:
    """Every source file of the core, ``version.py`` included; ``testing`` only when asked (it needs pytest)."""
    concepts = [c for c in CORE if include_testing or c != "testing"]
    files = [SRC / "version.py"]
    for concept in concepts:
        files += [p for p in (SRC / concept).rglob("*.py") if "__pycache__" not in p.parts]
    return files


def contract_files() -> list[Path]:
    files: list[Path] = []
    for contract in CONTRACTS:
        path = SRC.joinpath(*contract.split("."))
        files += [p for p in path.rglob("*.py") if "__pycache__" not in p.parts] if path.is_dir() else [path.with_suffix(".py")]
    return files


def module_name(path: Path) -> str:
    return ".".join(("substrate", *path.relative_to(SRC).with_suffix("").parts)).removesuffix(".__init__")
