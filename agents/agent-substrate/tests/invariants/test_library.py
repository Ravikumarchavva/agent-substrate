"""Invariant register — the package is a library (rows I31–I35).

``pip install agent-substrate`` gives the engine: no database driver, no vendor SDK, no web framework, no logging
handlers. Each of those is an extra named for what it enables, and an adapter needs only its own.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "substrate"

#: What a core install must never bring in: every extra's packages, by import name.
_NOT_THE_ENGINES = (
    "asyncpg sqlalchemy psycopg redis apscheduler fastapi starlette uvicorn openai anthropic google mcp rich "
    "prompt_toolkit kokoro soundfile tiktoken yaml PIL httpx httpx2 pydantic_settings pythonjsonlogger msgspec "
    "greenlet numpy jwt pytest pypdfium2 pypdfium2_raw rapidocr onnxruntime cv2"
).split()

_CORE = "types telemetry tools models stores documents workspace safety context middleware runtime agents".split()


def _loaded_by(*imports: str) -> set[str]:
    """The top-level modules a fresh interpreter has loaded after ``import <imports>``."""
    code = (
        "import sys, importlib\n"
        f"for name in {list(imports)!r}: importlib.import_module(name)\n"
        "print(' '.join(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return set(out.stdout.split())


def test_i31_the_core_install_is_the_engine_and_nothing_else() -> None:
    """``pip install agent-substrate`` brings pydantic, the OpenTelemetry API and ``pypdfium2`` (so a plain install reads PDFs). A driver,
    an SDK or a web framework in the base dependencies is installed by every user who wanted none of them."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    names = {
        d.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
        for d in project["dependencies"]
    }
    assert names == {"pydantic", "opentelemetry-api", "pypdfium2"}, (
        f"the base dependencies are {sorted(names)}"
    )


def test_i31_importing_the_engine_loads_no_driver_sdk_or_framework() -> None:
    """Every concept package of the core, imported in a fresh interpreter, loads none of what an extra provides."""
    loaded = _loaded_by("substrate", *[f"substrate.{c}" for c in _CORE])
    assert not loaded & set(_NOT_THE_ENGINES), (
        f"the engine imported {sorted(loaded & set(_NOT_THE_ENGINES))}"
    )


def test_i32_an_adapter_package_imports_only_what_it_is_asked_for() -> None:
    """``substrate.integrations`` and its vendor packages import nothing until a name is used: asking for the
    Anthropic client must not require the OpenAI SDK, nor MCP, nor Redis."""
    loaded = _loaded_by(
        "substrate.integrations",
        "substrate.integrations.llm",
        "substrate.integrations.tools",
    )
    leaked = loaded & {
        "openai",
        "anthropic",
        "google",
        "mcp",
        "redis",
        "kokoro",
        "tiktoken",
        "asyncpg",
        "fastapi",
    }
    assert not leaked, f"importing the adapter packages loaded {sorted(leaked)}"


def _mentions_setup_logging(path: Path) -> list[ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        n
        for n in ast.walk(tree)
        if (isinstance(n, ast.Name) and n.id == "setup_logging")
        or (isinstance(n, ast.alias) and n.name == "setup_logging")
    ]


#: The application entry points that may configure logging: the terminal console (and the logger module itself). The
#: platform and the services in apps/ configure their own.
_ENTRY_POINTS = {"console/app.py", "logger.py"}


def test_i33_a_library_module_never_configures_logging() -> None:
    """A library emits (``logging.getLogger(__name__)``); the application decides where the records go. Configuring
    handlers at import — as 118 modules once did, writing a rotating file into whatever directory the process
    started in — is a side effect nobody asked for. Only application entry points call ``setup_logging``."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in _ENTRY_POINTS:
            continue
        if _mentions_setup_logging(path):
            offenders.append(rel)
    assert not offenders, f"modules that configure logging: {offenders}"


def test_i33_an_entry_point_configures_logging_when_started_not_when_imported() -> None:
    """The permitted callers run ``setup_logging`` inside a function, never as a statement of the module."""
    offenders = []
    for rel in _ENTRY_POINTS - {"logger.py"}:
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Expr, ast.Assign)) and any(
                isinstance(n, ast.Call) and getattr(n.func, "id", "") == "setup_logging"
                for n in ast.walk(node)
            ):
                offenders.append(rel)
    assert not offenders, (
        f"modules that configure logging as they are imported: {offenders}"
    )


def test_i34_importing_substrate_installs_no_log_handler() -> None:
    """The library owns no handler on the ``substrate`` logger until an application adds one (a ``NullHandler``
    keeps Python from printing records the application did not ask to see)."""
    code = (
        "import logging, substrate.stores, substrate.runtime, substrate.integrations.llm\n"
        "handlers = logging.getLogger('substrate').handlers\n"
        "print(sorted(type(h).__name__ for h in handlers), logging.getLogger().handlers == [])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout
    assert out.strip() == "['NullHandler'] True", out


def test_i35_every_name_the_package_exports_resolves() -> None:
    """``from substrate import X`` is the first thing a user writes. A name in ``__all__`` that does not resolve fails
    there, not in a test of the thing itself. Only a name that lives in ``integrations`` or ``server`` may be missing — and
    only because its extra (a vendor SDK, FastAPI) is not installed."""
    code = (
        "import substrate\n"
        "missing = []\n"
        "for name in substrate.__all__:\n"
        "    try: getattr(substrate, name)\n"
        "    except ImportError: missing.append(name)\n"
        "print(' '.join(missing), '|', ' '.join(n for n, (mod, _) in substrate._LAZY.items() if mod.startswith(('substrate.integrations', 'substrate.server'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout
    missing, adapters = (part.split() for part in out.split("|"))
    assert set(missing) <= set(adapters), (
        f"names the engine itself should provide do not resolve: {sorted(set(missing) - set(adapters))}"
    )
