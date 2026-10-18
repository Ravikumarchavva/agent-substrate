"""``module:attr`` — how a command line names a Python object, as ``uvicorn`` and ``langgraph.json`` do."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


def load(reference: str, *, cwd: Path | None = None) -> Any:
    """The object ``reference`` names: ``package.module:attribute`` (dots in the attribute walk into it).

    The working directory is importable, so ``my_agent:agent`` works from the project that holds ``my_agent.py``."""
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"expected 'module:attribute', got {reference!r}")
    root = str(cwd or Path.cwd())
    if root not in sys.path:
        sys.path.insert(0, root)
    target: Any = importlib.import_module(module_name)
    for part in attribute.split("."):
        try:
            target = getattr(target, part)
        except AttributeError:
            raise ValueError(
                f"{module_name!r} has no attribute {attribute!r}"
            ) from None
    return target


__all__ = ["load"]
