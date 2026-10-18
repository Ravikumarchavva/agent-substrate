"""The shape of the kernel, as the structure tests check it.

``CONCEPTS`` lists the kernel's concepts from the bottom up: a concept may import the ones before it, never the ones
after (``tests/invariants/test_structure.py`` and the ``kernel-concepts`` import-linter contract hold the same order).

``CONTRACTS`` are the modules someone implementing a port depends on: the protocols and the value types in their
signatures. They stay free of I/O, of the engine, and of any vendor (``tests/invariants/test_contracts.py``).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "loamz" / "agents"
KERNEL = SRC / "kernel"

CONCEPTS = (
    "caller",
    "version",
    "errors",
    "types",
    "telemetry",
    "tools",
    "models",
    "ports",
    "safety",
    "context",
    "middleware",
    "runtime",
    "agents",
    "events",
    "wire",
)
"""Bottom-up. ``caller``, ``version`` and ``errors`` share the bottom rank, as do ``events`` and ``wire`` the top."""

RANK = {
    **{c: 0 for c in ("caller", "version", "errors")},
    **{c: i for i, c in enumerate(CONCEPTS[3:-2], start=1)},
    "events": len(CONCEPTS),
    "wire": len(CONCEPTS),
}

CONTRACTS = (
    "types",
    "tools.protocols",
    "tools.approval",
    "tools.chain",
    "tools.skills",
    "models.protocols",
    "ports",
    "safety.protocols",
    "context.protocols",
    "middleware.stage",
    "runtime.agent",
    "runtime.channel",
    "runtime.communication",
    "runtime.effects",
    "runtime.inbox",
    "runtime.message",
    "runtime.scheduler",
    "runtime.store",
    "runtime.supervisor",
    "events.bus",
    "events.envelope",
    "events.catalog",
)

PUBLIC_PACKAGES = (
    "loamz.agents",
    "loamz.agents.kernel.types",
    "loamz.agents.kernel.errors",
    "loamz.agents.kernel.telemetry",
    "loamz.agents.kernel.tools",
    "loamz.agents.kernel.models",
    "loamz.agents.kernel.ports",
    "loamz.agents.kernel.safety",
    "loamz.agents.kernel.context",
    "loamz.agents.kernel.middleware",
    "loamz.agents.kernel.runtime",
    "loamz.agents.kernel.agents",
    "loamz.agents.kernel.events",
    "loamz.agents.kernel.wire",
    "loamz.agents.stores",
    "loamz.agents.stores.workspace",
)
"""The packages whose ``__all__`` is the public API (``tests/invariants/public_api.json``)."""


def kernel_files() -> list[Path]:
    return [p for p in KERNEL.rglob("*.py") if "__pycache__" not in p.parts]


def contract_files() -> list[Path]:
    files: list[Path] = []
    for contract in CONTRACTS:
        path = KERNEL.joinpath(*contract.split("."))
        files += (
            [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]
            if path.is_dir()
            else [path.with_suffix(".py")]
        )
    return files


def module_name(path: Path) -> str:
    return ".".join(("loamz", "agents", *path.relative_to(SRC).with_suffix("").parts)).removesuffix(
        ".__init__"
    )


def concept_of(path: Path) -> str:
    return path.relative_to(KERNEL).parts[0].removesuffix(".py")
