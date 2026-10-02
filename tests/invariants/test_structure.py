"""Invariant register — structure (rows I26, I27, I28, I30).

The core package is the engine: one folder per concept (``tests/_layout.py``), each holding its contracts beside
its built-in implementation. These rows keep it a library: it loads no vendor SDK or driver, its concepts only
import downward, the contracts never reach into the engine, and its public API changes only on purpose.

Row I30 is the meta-row. The audit found a file that promised "the same suite
is run against those implementations" and never was, while the three runtime
backends drifted apart. A promise that nothing executes is how that happens, so
the register asserts the promise itself.
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

from tests._layout import CORE, REPO_ROOT, SRC, contract_files, core_files, module_name

SNAPSHOT = Path(__file__).parent / "public_api.json"

# What the core is allowed to import from outside the standard library.
# pydantic for validation, typing_extensions for back-ported typing, and the
# OpenTelemetry *API* (never the SDK — the API is a no-op until an application
# configures an SDK, which is what lets a library instrument itself for free).
#
# ``confusable_homoglyphs`` is the one pure-Python data library admitted
# beyond that: the homoglyph skeleton in ``safety.normalize`` is a prompt-
# injection defence the engine owns, it does no I/O, and reimplementing the
# UTS-39 table by hand would be a worse trade than the dependency.
_ALLOWED_THIRD_PARTY = {"pydantic", "typing_extensions", "opentelemetry", "confusable_homoglyphs"}


def _imports(path: Path, *, type_checking: bool = True) -> list[str]:
    """Every module a file imports, from the AST — including imports inside functions, which is exactly
    where a lazy heavy dependency hides. ``type_checking=False`` leaves out ``if TYPE_CHECKING:`` blocks."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped: set[int] = set()
    if not type_checking:
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
                skipped.update(id(n) for child in node.body for n in ast.walk(child))
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found.append(node.module)
    return found


def test_i26_the_core_imports_only_its_allowed_third_party_set() -> None:
    """The core is the engine, so it has to be installable and importable
    without a vendor SDK, a model runtime, or a database driver."""
    offenders: dict[str, set[str]] = {}
    for path in core_files():
        third_party = {
            root
            for root in (m.split(".")[0] for m in _imports(path))
            if root not in _ALLOWED_THIRD_PARTY and root != "substrate" and root not in sys.stdlib_module_names
        }
        if third_party:
            offenders[str(path.relative_to(REPO_ROOT))] = third_party
    assert not offenders, (
        "the core may depend only on pydantic, the OpenTelemetry API and confusable_homoglyphs; "
        "anything needing a third-party SDK belongs in an integration:\n"
        + "\n".join(f"  {where}: {sorted(roots)}" for where, roots in offenders.items())
    )


def test_the_core_imports_nothing_outside_itself() -> None:
    """The core is what every integration and application builds on, so it can name none of them."""
    allowed = {f"substrate.{c}" for c in CORE} | {"substrate.version", "substrate"}
    offenders = {
        f"{path.relative_to(REPO_ROOT)}: {module}"
        for path in core_files(include_testing=True)
        for module in _imports(path)
        if module.startswith("substrate") and ".".join(module.split(".")[:2]) not in allowed
    }
    assert not offenders, "the core imports from outside it:\n  " + "\n  ".join(sorted(offenders))


def test_concepts_only_import_the_concepts_below_them() -> None:
    """``CORE`` is ordered bottom-up: types, then tools, models, stores … up to agents. A concept that imported
    one above it would make the order a cycle, and the bottom of the engine would drag the top along. Imports
    made only for type checking are exempt — they never run."""
    rank = {c: i for i, c in enumerate(CORE)}
    offenders: set[str] = set()
    for path in core_files(include_testing=True):
        own = path.relative_to(SRC).parts[0]
        if own not in rank:
            continue
        for module in _imports(path, type_checking=False):
            parts = module.split(".")
            if parts[0] == "substrate" and len(parts) > 1 and parts[1] in rank and rank[parts[1]] > rank[own]:
                offenders.add(f"{path.relative_to(REPO_ROOT)}: {own} imports {module}")
    assert not offenders, "a concept imports one above it:\n  " + "\n  ".join(sorted(offenders))


def test_the_core_never_imports_its_own_test_support() -> None:
    """``substrate.testing`` holds conformance suites and doubles. Production code that imported
    it would make pytest a runtime dependency of the engine."""
    support = SRC / "testing"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC.rglob("*.py")
        if "__pycache__" not in path.parts
        and support not in path.parents
        and any(m.startswith("substrate.testing") for m in _imports(path))
    ]
    assert not offenders, f"production code imports substrate.testing: {offenders}"


def test_i27_the_contracts_never_import_the_engine() -> None:
    """The contracts are what someone implementing a port depends on. If one reaches into the engine,
    implementing a port drags the whole engine along."""
    contracts = {module_name(p) for p in contract_files()}
    offenders: set[str] = set()
    for path in contract_files():
        for module in _imports(path):
            if module.startswith("substrate.") and module not in contracts and module != "substrate.types":
                offenders.add(f"{path.relative_to(REPO_ROOT)}: {module}")
    assert not offenders, "a contract imports the engine:\n  " + "\n  ".join(sorted(offenders))


def test_i28_the_public_api_matches_its_snapshot() -> None:
    """Every addition or removal in the public API shows up as a diff in
    ``public_api.json``, so it is reviewed rather than noticed later.

    The public API is what each concept package exports. This rewrite changes it
    deliberately and often — the snapshot is meant to be updated in the same
    commit as the change, never regenerated blindly.
    """
    expected = json.loads(SNAPSHOT.read_text())
    problems: list[str] = []
    for concept in CORE:
        if concept == "testing":
            continue
        name = f"substrate.{concept}"
        current = set(getattr(importlib.import_module(name), "__all__", ()))
        wanted = set(expected.get(name, ()))
        if current != wanted:
            problems.append(f"{name}\n    added:   {sorted(current - wanted)}\n    removed: {sorted(wanted - current)}")
    assert not problems, (
        "the public API changed:\n  " + "\n  ".join(problems)
        + f"\nIf intended, update {SNAPSHOT.relative_to(REPO_ROOT)} in this commit."
    )


# Ports an outside party can implement, and what each one's conformance suite is called
# (``substrate/testing/conformance/``). A port with no suite is a promise nothing checks.
_PORTS = (
    "RuntimeStore",
    "ThreadStore",
    "MemoryStore",
    "VectorStore",
    "GraphStore",
    "FileStore",
    "TaskStore",
    "ChatModel",
    "EmbeddingModel",
    "DocumentExtractor",
)
_SUITES_DIR = SRC / "testing" / "conformance"


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
    assert "ThreadStore" in suites, "the history-provider conformance suite has gone missing"
    assert "FileStore" in suites, "the FileStore conformance suite has gone missing"
    assert "TaskStore" in suites, "the TaskStore conformance suite has gone missing"
    assert "GraphStore" in suites, "the GraphStore conformance suite has gone missing"
    for port in ("ChatModel", "EmbeddingModel", "DocumentExtractor"):
        assert port in suites, f"the {port} conformance suite has gone missing"
    assert "VectorStore" in suites, "the vector-store conformance suite has gone missing"
    shipped = {
        "RuntimeStore": ("SqlRuntimeStoreOnSqlite", "PostgresRuntimeStore"),
        "MemoryStore": ("LocalFilesystemMemoryStore", "DurableMemoryStore", "LanceMemoryStore"),
        "ThreadStore": ("TestThreads",),
        "FileStore": ("WorkspaceFileStore", "S3FileStore"),
        "TaskStore": ("LocalFilesystemTaskStore", "PgTaskStore"),
        "GraphStore": ("LocalFilesystemGraphStore", "LanceGraphStore"),
        "ChatModel": ("OpenAICompatibleClient", "OpenAIClient", "AnthropicClient", "GeminiClient"),
        "EmbeddingModel": ("OpenAIEmbeddingClient", "GeminiEmbeddingClient", "SentenceTransformersEmbeddingClient", "EmbeddingRerankerTextEmbeddingClient"),
        "DocumentExtractor": ("LocalDocumentExtractor", "ServiceBackedDocumentExtractor"),
        "VectorStore": ("LocalFilesystemVectorStore", "LanceDBVectorStore", "PgVectorStore"),
    }
    for port, implementations in shipped.items():
        runners = " ".join(_classes_running(suites[port]))
        for implementation in implementations:
            assert implementation.lower() in runners.lower(), (
                f"{implementation} is a {port} but no test class runs {suites[port]} against it; runners: {runners or 'none'}"
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
        f"import {', '.join('substrate.' + c for c in CORE if c != 'testing')}\n"
        "roots = {m.split('.')[0] for m in set(sys.modules) - before}\n"
        "print(sorted(r for r in roots if r not in sys.stdlib_module_names and not r.startswith('_') and r != 'substrate'))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO_ROOT).stdout
    loaded = set(ast.literal_eval(out.strip().splitlines()[-1]))
    # Dependencies of the allowed packages themselves: pydantic's, and opentelemetry-api's.
    transitive = {"annotated_types", "pydantic_core", "typing_inspection", "importlib_metadata", "zipp"}
    unexpected = loaded - _ALLOWED_THIRD_PARTY - transitive
    assert not unexpected, f"importing the core loaded packages outside its allowed set: {sorted(unexpected)}"
