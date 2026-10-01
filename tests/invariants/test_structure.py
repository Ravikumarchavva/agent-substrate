"""Invariant register — structure (rows I26, I27, I28, I30).

The merge pulls a 17.6k-line engine into ``kernel``. Three of these rows are
tripwires for that move: they pass today and must keep passing as the engine
arrives, which is what stops a vendor SDK riding in with it.

Row I30 is the meta-row. The audit found a file that promised "the same suite
is run against those implementations" and never was, while the three runtime
backends drifted apart. A promise that nothing executes is how that happens, so
the register asserts the promise itself.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL = REPO_ROOT / "src" / "substrate" / "kernel"
SNAPSHOT = Path(__file__).parent / "public_api.json"

# What the kernel is allowed to import from outside the standard library.
# pydantic for validation, typing_extensions for back-ported typing, and the
# OpenTelemetry *API* (never the SDK — the API is a no-op until an application
# configures an SDK, which is what lets a library instrument itself for free).
#
# ``confusable_homoglyphs`` is the one pure-Python data library admitted
# beyond that: the homoglyph skeleton in ``safety.normalize`` is a prompt-
# injection defence the engine owns, it does no I/O, and reimplementing the
# UTS-39 table by hand would be a worse trade than the dependency.
_ALLOWED_THIRD_PARTY = {"pydantic", "typing_extensions", "opentelemetry", "confusable_homoglyphs"}


def _kernel_files() -> list[Path]:
    """The kernel's production code. ``kernel/testing`` is test support (it needs pytest to
    import), shipped beside the ports it verifies and used only by tests — see
    ``test_the_kernel_never_imports_its_own_test_support``."""
    return [p for p in KERNEL.rglob("*.py") if "__pycache__" not in p.parts and "testing" not in p.relative_to(KERNEL).parts[:1]]


def _imported_roots(path: Path) -> set[str]:
    """Top-level package of every import in a file, from the AST.

    Parsed rather than pattern-matched on line prefixes: the existing
    architecture test misses indented imports inside functions, which is
    exactly where a lazy heavy dependency hides.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays inside the package
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_i26_the_kernel_imports_only_its_allowed_third_party_set() -> None:
    """The kernel is the engine, so it has to be installable and importable
    without a vendor SDK, a model runtime, or a database driver."""
    offenders: dict[str, set[str]] = {}
    for path in _kernel_files():
        third_party = {
            root
            for root in _imported_roots(path)
            if root not in _ALLOWED_THIRD_PARTY
            and root != "substrate"
            and root not in sys.stdlib_module_names
        }
        if third_party:
            offenders[str(path.relative_to(REPO_ROOT))] = third_party
    assert not offenders, (
        "the kernel may depend only on pydantic, the OpenTelemetry API and confusable_homoglyphs; "
        "anything needing a third-party SDK belongs in integrations/:\n"
        + "\n".join(f"  {where}: {sorted(roots)}" for where, roots in offenders.items())
    )


def test_the_kernel_never_imports_its_own_test_support() -> None:
    """``kernel/testing`` holds conformance suites and doubles. Production code that imported
    it would make pytest a runtime dependency of the engine."""
    support = KERNEL / "testing"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src" / "substrate").rglob("*.py")
        if "__pycache__" not in path.parts
        and support not in path.parents
        and "substrate.kernel.testing" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"production code imports substrate.kernel.testing: {offenders}"


def test_i27_abstractions_never_imports_the_engine() -> None:
    """``abstractions`` is what an adapter author depends on. If it reaches back
    into the engine, implementing a port drags the whole engine along."""
    abstractions = KERNEL / "abstractions"
    assert abstractions.is_dir(), "kernel/abstractions does not exist"

    engine_packages = {
        p.name for p in KERNEL.iterdir() if p.is_dir() and p.name not in ("abstractions", "__pycache__")
    }
    offenders: dict[str, set[str]] = {}
    for path in abstractions.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reached: set[str] = set()
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module and module.startswith("substrate.kernel."):
                area = module.removeprefix("substrate.kernel.").split(".")[0]
                if area in engine_packages:
                    reached.add(area)
        if reached:
            offenders[str(path.relative_to(REPO_ROOT))] = reached
    assert not offenders, (
        "kernel/abstractions reached into the engine:\n"
        + "\n".join(f"  {where}: {sorted(areas)}" for where, areas in offenders.items())
    )


def test_i28_the_public_api_matches_its_snapshot() -> None:
    """Every addition or removal in the public API shows up as a diff in
    ``public_api.json``, so it is reviewed rather than noticed later.

    This rewrite changes the API deliberately and often — the snapshot is meant
    to be updated in the same commit as the change, never regenerated blindly.
    """
    import substrate.kernel.abstractions as abstractions

    current = sorted(abstractions.__all__)
    expected = json.loads(SNAPSHOT.read_text())["substrate.kernel.abstractions"]

    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    assert not added and not removed, (
        "the public API of substrate.kernel.abstractions changed.\n"
        f"  added:   {added}\n"
        f"  removed: {removed}\n"
        f"If intended, update {SNAPSHOT.relative_to(REPO_ROOT)} in this commit."
    )


# Ports an outside party can implement, and what each one's conformance suite is called
# (``kernel/testing/conformance/``). A port with no suite is a promise nothing checks.
_PORTS = (
    "RuntimeStore",
    "HistoryProvider",
    "MemoryStore",
    "VectorStore",
    "GraphStore",
    "ObjectStore",
    "TaskStore",
    "LLMClient",
    "EmbeddingClient",
    "DocumentExtractor",
)
_SUITES_DIR = KERNEL / "testing" / "conformance"


def _suite_classes() -> dict[str, str]:
    """``{port: suite class name}`` for every ``<Port>Conformance`` class that exists."""
    found: dict[str, str] = {}
    for path in _SUITES_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Conformance"):
                found[node.name.removesuffix("Conformance")] = node.name
    return found


def _classes_running(suite: str) -> list[str]:
    """Test classes under ``tests/`` that subclass ``suite``."""
    running: list[str] = []
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id == suite) or (isinstance(b, ast.Attribute) and b.attr == suite)
                for b in node.bases
            ):
                running.append(f"{path.relative_to(REPO_ROOT)}::{node.name}")
    return running


def test_i30_every_implementation_of_a_port_with_a_suite_runs_it() -> None:
    """The row that keeps the other rows honest. A file once promised "the same suite is run
    against those implementations" and never was, while three backends drifted apart.

    For each port that has a suite, every shipped implementation must be run through it.
    """
    suites = _suite_classes()
    assert "RuntimeStore" in suites, "the runtime-store conformance suite has gone missing"
    assert "MemoryStore" in suites, "the memory-store conformance suite has gone missing"
    assert "HistoryProvider" in suites, "the history-provider conformance suite has gone missing"
    assert "ObjectStore" in suites, "the ObjectStore conformance suite has gone missing"
    assert "TaskStore" in suites, "the TaskStore conformance suite has gone missing"
    assert "GraphStore" in suites, "the GraphStore conformance suite has gone missing"
    assert "VectorStore" in suites, "the vector-store conformance suite has gone missing"
    shipped = {
        "RuntimeStore": ("SqliteRuntimeStore", "PostgresRuntimeStore"),
        "MemoryStore": ("LocalFilesystemMemoryStore", "DurableMemoryStore", "LanceMemoryStore"),
        "HistoryProvider": ("LocalFilesystemHistoryProvider", "DurableHistoryProvider"),
        "ObjectStore": ("WorkspaceFileStore", "S3FileStore"),
        "TaskStore": ("LocalFilesystemTaskStore", "PgTaskStore"),
        "GraphStore": ("LocalFilesystemGraphStore", "LanceGraphStore"),
        "VectorStore": ("LocalFilesystemVectorStore", "LanceDBVectorStore", "PgVectorStore"),
    }
    for port, implementations in shipped.items():
        runners = " ".join(_classes_running(suites[port]))
        for implementation in implementations:
            assert implementation.lower() in runners.lower(), (
                f"{implementation} is a {port} but no test class runs {suites[port]} against it; runners: {runners or 'none'}"
            )


@pytest.mark.xfail(
    strict=True,
    reason="I30: every store port has a conformance suite; the LLM-client, embedding-client "
    "and document-extractor ports do not yet.",
)
def test_i30_every_port_has_a_conformance_suite() -> None:
    missing = [port for port in _PORTS if port not in _suite_classes()]
    assert not missing, f"ports with no conformance suite: {missing}"


def test_the_core_install_carries_the_opentelemetry_api_and_nothing_that_exports() -> None:
    """The engine instruments itself through ``opentelemetry-api``, which does nothing until a
    host configures an SDK. The SDK, the exporter and the web-framework instrumentation are the
    host's choice — the reference server installs them through its extra — so a plain install of the
    engine does not pull them in."""
    import tomllib

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    core = {d.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip() for d in project["dependencies"]}
    assert "opentelemetry-api" in core
    exporting = sorted(d for d in core if d.startswith("opentelemetry-") and d != "opentelemetry-api")
    assert not exporting, f"core dependencies that belong to the host, not the engine: {exporting}"
    server = " ".join(project["optional-dependencies"]["server"])
    assert "opentelemetry-sdk" in server, "the reference server lost the SDK it configures"


def test_i26_importing_the_whole_engine_loads_only_the_allowed_third_party_set() -> None:
    """The AST check above sees what each file names; this one sees what actually loads. A module that
    reached a vendor SDK, a logging stack or a database driver through a helper would pass the first and
    fail this."""
    import subprocess

    probe = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import substrate.kernel, substrate.kernel.runtime, substrate.kernel.agents, substrate.kernel.tools\n"
        "import substrate.kernel.context, substrate.kernel.flows, substrate.kernel.middleware, substrate.kernel.llm\n"
        "import substrate.kernel.storage, substrate.kernel.workspace, substrate.kernel.safety, substrate.kernel.telemetry\n"
        "roots = {m.split('.')[0] for m in set(sys.modules) - before}\n"
        "print(sorted(r for r in roots if r not in sys.stdlib_module_names and not r.startswith('_') and r != 'substrate'))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO_ROOT).stdout
    loaded = set(ast.literal_eval(out.strip().splitlines()[-1]))
    # Dependencies of the allowed packages themselves: pydantic's, and opentelemetry-api's.
    transitive = {"annotated_types", "pydantic_core", "typing_inspection", "importlib_metadata", "zipp"}
    unexpected = loaded - _ALLOWED_THIRD_PARTY - transitive
    assert not unexpected, f"importing the kernel loaded packages outside its allowed set: {sorted(unexpected)}"
