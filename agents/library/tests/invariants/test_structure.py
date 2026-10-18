"""Invariant register — structure (rows I26, I27, I28, I30).

The kernel is the engine: one folder per concept (``tests/_layout.py``), each holding its contracts beside its built-in
implementation. These rows keep it a library: it loads no vendor SDK or driver, its concepts only import downward,
the contracts never reach into the engine, and its public API changes only on purpose.

Row I30 is the meta-row. The audit found a file that promised "the same suite is run against those implementations"
and never was, while the three runtime backends drifted apart. A promise that nothing executes is how that happens,
so the register asserts the promise itself.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from tests._layout import (
    KERNEL,
    PUBLIC_PACKAGES,
    RANK,
    REPO_ROOT,
    SRC,
    concept_of,
    contract_files,
    kernel_files,
    module_name,
)

SNAPSHOT = Path(__file__).parent / "public_api.json"

# What the kernel may import from outside the standard library: pydantic for validation, typing_extensions, the
# OpenTelemetry *API* (never the SDK: the API is a no-op until an application configures one) and
# ``confusable_homoglyphs`` (the homoglyph skeleton in ``safety.normalize``: a prompt-injection defence the engine owns,
# pure data, no I/O). ``loamz`` is ours: the kernel stands on ``loamz.data.storage`` (its errors and SQL).
_ALLOWED_THIRD_PARTY = {
    "pydantic",
    "typing_extensions",
    "opentelemetry",
    "confusable_homoglyphs",
    "loamz",
}


def _imports(path: Path, *, type_checking: bool = True) -> list[str]:
    """Every module a file imports, from the AST, including imports inside functions (where a lazy heavy dependency
    hides). ``type_checking=False`` leaves out ``if TYPE_CHECKING:`` blocks."""
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


def test_i26_the_kernel_imports_only_its_allowed_third_party_set() -> None:
    """The kernel is the engine, so it has to be installable and importable without a vendor SDK, a model runtime or
    a database driver."""
    offenders: dict[str, set[str]] = {}
    for path in kernel_files():
        third_party = {
            root
            for root in (m.split(".")[0] for m in _imports(path))
            if root not in _ALLOWED_THIRD_PARTY and root not in sys.stdlib_module_names
        }
        if third_party:
            offenders[str(path.relative_to(REPO_ROOT))] = third_party
    assert not offenders, (
        "the kernel may depend only on pydantic, the OpenTelemetry API, confusable_homoglyphs and loamz.data.storage; "
        "anything needing a third-party SDK belongs in providers/ or toolkit/:\n"
        + "\n".join(f"  {where}: {sorted(roots)}" for where, roots in offenders.items())
    )


def test_the_kernel_imports_nothing_of_loamz_but_itself_and_storage() -> None:
    """Everything else in loamz.agents builds on the kernel, so the kernel can name none of it; of Loamz Data it uses
    only the embeddable storage (never the client or the server)."""
    offenders = {
        f"{path.relative_to(REPO_ROOT)}: {module}"
        for path in kernel_files()
        for module in _imports(path)
        if module.startswith("loamz.")
        and not module.startswith(("loamz.agents.kernel", "loamz.data.storage"))
    }
    assert not offenders, "the kernel imports from outside it:\n  " + "\n  ".join(sorted(offenders))


def test_concepts_only_import_the_concepts_below_them() -> None:
    """Concepts are ordered bottom-up (``tests/_layout.CONCEPTS``). A concept that imported one above it would make
    the order a cycle. Imports made only for type checking are exempt: they never run."""
    offenders: set[str] = set()
    for path in kernel_files():
        own = concept_of(path)
        if own not in RANK:
            continue
        for module in _imports(path, type_checking=False):
            parts = module.split(".")
            if parts[:3] == ["loamz", "agents", "kernel"] and len(parts) > 3:
                other = parts[3]
                if other in RANK and RANK[other] > RANK[own]:
                    offenders.add(f"{path.relative_to(REPO_ROOT)}: {own} imports {module}")
    assert not offenders, "a concept imports one above it:\n  " + "\n  ".join(sorted(offenders))


def test_production_code_never_imports_the_test_support() -> None:
    """``loamz.agents.testing`` holds conformance suites and doubles; production code that imported it would make
    pytest a runtime dependency."""
    support = SRC / "testing"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC.rglob("*.py")
        if "__pycache__" not in path.parts
        and support not in path.parents
        and any(m.startswith("loamz.agents.testing") for m in _imports(path))
    ]
    assert not offenders, f"production code imports loamz.agents.testing: {offenders}"


def test_the_data_client_stays_behind_the_data_extra_and_importing_the_engine_never_loads_it() -> (
    None
):
    """Governed data is Loamz Data's: only ``loamz.agents.data`` (the ``data`` extra) may name ``loamz.data.client``,
    and importing the kernel, the stores, the providers or the toolkit must not load it."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).parts[0] != "data"
        and any(m.startswith("loamz.data.client") for m in _imports(path))
    ]
    assert not offenders, "only loamz.agents.data may import the data client:\n  " + "\n  ".join(
        offenders
    )
    program = (
        "import sys, loamz.agents.kernel.agents, loamz.agents.stores, loamz.agents.providers, loamz.agents.toolkit\n"
        "assert 'loamz.data.client' not in sys.modules, 'importing the engine loaded the data client'\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr


def test_no_object_store_credentials_or_s3_client_belong_to_the_library() -> None:
    """Files are Loamz Data's and clients move bytes on URLs it signs, so the library holds no object-store
    credentials: no S3 client is named anywhere in it and the package declares no ``s3`` extra."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC.rglob("*.py")
        if {m.split(".")[0] for m in _imports(path)} & {"aiobotocore", "botocore", "boto3"}
    ]
    assert not offenders, "no S3 client in the library:\n  " + "\n  ".join(offenders)
    extras = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    assert "s3" not in extras and "data" in extras


def test_i27_the_contracts_never_import_the_engine() -> None:
    """The contracts are what someone implementing a port depends on. If one reaches into the engine, implementing a
    port drags the whole engine along."""
    contracts = {module_name(p) for p in contract_files()}
    offenders: set[str] = set()
    for path in contract_files():
        for module in _imports(path):
            if (
                module.startswith("loamz.agents.")
                and module not in contracts
                and module not in ("loamz.agents.kernel.types", "loamz.agents.kernel.errors")
            ):
                offenders.add(f"{path.relative_to(REPO_ROOT)}: {module}")
    assert not offenders, "a contract imports the engine:\n  " + "\n  ".join(sorted(offenders))


def _public_api() -> dict[str, list[str]]:
    return {
        name: sorted(getattr(importlib.import_module(name), "__all__", ()))
        for name in PUBLIC_PACKAGES
    }


def test_i28_the_public_api_matches_its_snapshot() -> None:
    """Every addition or removal in the public API shows up as a diff in ``public_api.json``, so it is reviewed rather
    than noticed later. Update the snapshot in the same commit as an intended change
    (``python -m tests.invariants.test_structure``)."""
    expected = json.loads(SNAPSHOT.read_text())
    current = _public_api()
    problems = [
        f"{name}\n    added:   {sorted(set(current[name]) - set(expected.get(name, ())))}"
        f"\n    removed: {sorted(set(expected.get(name, ())) - set(current[name]))}"
        for name in current
        if set(current[name]) != set(expected.get(name, ()))
    ]
    assert not problems, (
        "the public API changed:\n  "
        + "\n  ".join(problems)
        + f"\nIf intended, update {SNAPSHOT.relative_to(REPO_ROOT)} in this commit."
    )


# Ports an outside party can implement, and the implementations this package ships. Long-term memory
# (``MemoryStore``) is a kernel port whose implementation and suite are Loamz Data's (loamz.data.storage).
_SHIPPED = {
    "RuntimeStore": ("TestRuntimeStore", "TestRuntimeStoreOnSqlite"),
    "ThreadStore": ("TestThreads", "TestBoundHistoryConforms"),
    "ShortTermMemory": ("TestSessionState", "TestRedisSessionStore"),
    "GraphStore": ("TestGraph",),
    "FileStore": ("TestMemoryFileStoreDouble",),
    "TaskStore": ("TestTasks",),
    "WorkspaceStore": ("TestWorkspaces",),
    "EventBus": ("InMemoryEventBus", "RedisEventBus"),
    "ChatModel": ("OpenAICompatibleClient", "OpenAIClient", "AnthropicClient", "GeminiClient"),
    "EmbeddingModel": (
        "OpenAIEmbeddingClient",
        "GeminiEmbeddingClient",
        "SentenceTransformersEmbeddingClient",
    ),
}
_SUITES_DIR = SRC / "testing" / "conformance"


def _suite_classes() -> dict[str, str]:
    """``{port: suite class name}`` for every ``<Port>Conformance`` class that exists."""
    found: dict[str, str] = {}
    for path in _SUITES_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Conformance"):
                found[node.name.removesuffix("Conformance")] = node.name
    return found


def _bases(node: ast.ClassDef) -> set[str]:
    return {
        b.id if isinstance(b, ast.Name) else b.attr
        for b in node.bases
        if isinstance(b, (ast.Name, ast.Attribute))
    }


def _classes_running(suite: str) -> list[str]:
    """Test classes under ``tests/`` that subclass ``suite`` (or a suite that extends it), with their source."""
    extended = {suite}
    for path in _SUITES_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and suite in _bases(node):
                extended.add(node.name)
    running: list[str] = []
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ClassDef) and _bases(node) & extended:
                running.append(
                    f"{path.relative_to(REPO_ROOT)}::{node.name} {ast.get_source_segment(source, node)}"
                )
    return running


def test_i30_every_port_has_a_conformance_suite() -> None:
    missing = [port for port in _SHIPPED if port not in _suite_classes()]
    assert not missing, f"ports with no conformance suite: {missing}"


def test_i30_every_implementation_of_a_port_with_a_suite_runs_it() -> None:
    """The row that keeps the other rows honest: for each port, every shipped implementation is run through its
    suite."""
    suites = _suite_classes()
    for port, implementations in _SHIPPED.items():
        runners = " ".join(_classes_running(suites[port]))
        for implementation in implementations:
            assert implementation.lower() in runners.lower(), (
                f"{implementation} is a {port} but no test class runs {suites[port]} against it"
            )


def test_the_core_install_carries_the_opentelemetry_api_and_nothing_that_exports() -> None:
    """The engine instruments itself through ``opentelemetry-api``, which does nothing until a host configures an SDK.
    The SDK and exporters are the server's (the ``server`` extra), so a plain install does not pull them in."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]

    def names(deps: list[str]) -> set[str]:
        return {d.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip() for d in deps}

    core = names(project["dependencies"])
    assert "opentelemetry-api" in core
    exporting = sorted(
        d for d in core if d.startswith("opentelemetry-") and d != "opentelemetry-api"
    )
    assert not exporting, f"core dependencies that belong to the host, not the engine: {exporting}"
    assert "opentelemetry-sdk" in names(project["optional-dependencies"]["server"])


def test_i26_importing_the_whole_kernel_loads_only_the_allowed_third_party_set() -> None:
    """The AST check above sees what each file names; this one sees what actually loads."""
    concepts = sorted(
        {p.relative_to(KERNEL).parts[0].removesuffix(".py") for p in kernel_files()} - {"__init__"}
    )
    probe = (
        "import sys\n"
        "before = set(sys.modules)\n"
        f"import {', '.join('loamz.agents.kernel.' + c for c in concepts)}\n"
        "roots = {m.split('.')[0] for m in set(sys.modules) - before}\n"
        "print(sorted(r for r in roots if r not in sys.stdlib_module_names and not r.startswith('_')))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    ).stdout
    loaded = set(ast.literal_eval(out.strip().splitlines()[-1]))
    transitive = {
        "annotated_types",
        "pydantic_core",
        "typing_inspection",
        "importlib_metadata",
        "zipp",
    }
    unexpected = loaded - _ALLOWED_THIRD_PARTY - transitive
    assert not unexpected, (
        f"importing the kernel loaded packages outside its allowed set: {sorted(unexpected)}"
    )


if __name__ == "__main__":  # regenerate the snapshot after an intended API change
    SNAPSHOT.write_text(json.dumps(_public_api(), indent=2) + "\n")
