"""CatalogScanner — convention-based discovery of catalog packages.

Walks directories looking for packages that follow a naming convention:
- ``tool.py``       → tool component (imports first ``BaseTool`` subclass)
- ``SKILL.md``      → skill component (parsed via SkillLoader._load_metadata)
- ``connector.py``  → connector component (imports first ``BaseConnector`` subclass)
- ``pipeline.py``   → pipeline step component

Packages live under the grouped subdirectories::

    catalog/tools/       ← BaseTool implementations
    catalog/skills/      ← SKILL.md prompt packages
    catalog/connectors/  ← external service connectors

A single folder may contain any combination of the above.

Usage::

    scanner = CatalogScanner()
    packages = scanner.discover()
    for pkg in packages:
        print(pkg.name, pkg.components)
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Type

logger = logging.getLogger("raavan.catalog.scanner")

ComponentKind = Literal["tool", "skill", "connector", "pipeline_step"]


@dataclass
class CatalogPackage:
    """A discovered catalog package and its detected components."""

    name: str
    path: Path
    components: Set[ComponentKind] = field(default_factory=set)
    tool_class: Optional[Type[Any]] = field(default=None, repr=False)
    skill_metadata: Optional[Any] = field(default=None, repr=False)
    connector_class: Optional[Type[Any]] = field(default=None, repr=False)
    config: Optional[Dict[str, Any]] = field(default=None, repr=False)


def _default_catalog_dirs() -> List[Path]:
    """Return the built-in catalog type subdirectories."""
    package_root = Path(__file__).resolve().parent
    # Scan the three typed subdirectories so packages are grouped by type
    return [
        package_root / "tools",
        package_root / "skills",
        package_root / "connectors",
    ]


class CatalogScanner:
    """Discover catalog packages by filesystem convention.

    Parameters
    ----------
    adapter_dirs
        Directories to scan.  Defaults to ``catalog/tools``, ``catalog/skills``,
        and ``catalog/connectors``.
    """

    def __init__(
        self,
        adapter_dirs: Optional[List[str | Path]] = None,
    ) -> None:
        self._dirs: List[Path] = []
        configured = _default_catalog_dirs() if adapter_dirs is None else adapter_dirs
        for d in configured:
            p = Path(d).expanduser().resolve()
            if p.is_dir():
                self._dirs.append(p)
            else:
                logger.debug("Catalog directory not found (skipping): %s", p)

        self._packages: Dict[str, CatalogPackage] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> List[CatalogPackage]:
        """Scan all configured directories for adapter packages.

        Returns a deduplicated list of ``CatalogPackage`` objects.
        First occurrence of a name wins (earlier dirs take priority).
        """
        found: Dict[str, CatalogPackage] = {}

        for base_dir in self._dirs:
            if not base_dir.is_dir():
                continue
            for child in sorted(base_dir.iterdir()):
                if not child.is_dir():
                    continue
                # Skip private / dunder directories
                if child.name.startswith("_"):
                    continue
                if child.name == "__pycache__":
                    continue

                pkg = self._scan_package(child)
                if pkg is None or not pkg.components:
                    continue

                if pkg.name in found:
                    logger.debug(
                        "Catalog package %r already discovered — skipping %s",
                        pkg.name,
                        child,
                    )
                else:
                    found[pkg.name] = pkg
                    logger.debug(
                        "Discovered catalog package: %r (%s) at %s",
                        pkg.name,
                        ", ".join(sorted(pkg.components)),
                        child,
                    )

        self._packages = found
        return list(found.values())

    def get(self, name: str) -> Optional[CatalogPackage]:
        """Return a discovered package by name."""
        return self._packages.get(name)

    def all(self) -> List[CatalogPackage]:
        """Return all discovered packages."""
        return list(self._packages.values())

    # ------------------------------------------------------------------
    # Internal scanning
    # ------------------------------------------------------------------

    def _scan_package(self, pkg_dir: Path) -> Optional[CatalogPackage]:
        """Detect components in a single adapter directory."""
        name = pkg_dir.name
        pkg = CatalogPackage(name=name, path=pkg_dir)

        # tool.py → tool
        tool_py = pkg_dir / "tool.py"
        if tool_py.is_file():
            tool_cls = self._load_tool_class(pkg_dir, tool_py)
            if tool_cls is not None:
                pkg.components.add("tool")
                pkg.tool_class = tool_cls

        # SKILL.md → skill
        skill_md = pkg_dir / "SKILL.md"
        if skill_md.is_file():
            meta = self._load_skill_metadata(pkg_dir, skill_md)
            if meta is not None:
                pkg.components.add("skill")
                pkg.skill_metadata = meta

        # connector.py → connector
        connector_py = pkg_dir / "connector.py"
        if connector_py.is_file():
            connector_cls = self._load_connector_class(pkg_dir, connector_py)
            if connector_cls is not None:
                pkg.components.add("connector")
                pkg.connector_class = connector_cls

        # pipeline.py → pipeline_step
        pipeline_py = pkg_dir / "pipeline.py"
        if pipeline_py.is_file():
            pkg.components.add("pipeline_step")

        return pkg

    # ------------------------------------------------------------------
    # Class loaders
    # ------------------------------------------------------------------

    def _load_tool_class(self, pkg_dir: Path, tool_py: Path) -> Optional[Type[Any]]:
        """Import tool.py and return the first ``BaseTool`` subclass found."""
        module_path = self._to_module_path(tool_py)
        if module_path is None:
            return None
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            logger.exception("Failed to import tool module: %s", module_path)
            return None

        from raavan.core.tools.base_tool import BaseTool

        for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, BaseTool) and obj is not BaseTool:
                return obj
        logger.warning("No BaseTool subclass found in %s", tool_py)
        return None

    def _load_connector_class(
        self, pkg_dir: Path, connector_py: Path
    ) -> Optional[Type[Any]]:
        """Import connector.py and return the first connector class found.

        Looks for any class whose name ends with ``Connector``.
        """
        module_path = self._to_module_path(connector_py)
        if module_path is None:
            return None
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            logger.exception("Failed to import connector module: %s", module_path)
            return None

        for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__name__.endswith("Connector") and obj.__module__ == mod.__name__:
                return obj
        logger.warning("No *Connector class found in %s", connector_py)
        return None

    def _load_skill_metadata(self, skill_dir: Path, skill_md: Path) -> Optional[Any]:
        """Parse SKILL.md using the existing SkillLoader helper."""
        try:
            from raavan.catalog._skill_loader import (
                SkillLoader,
            )

            loader = SkillLoader.__new__(SkillLoader)
            return loader._load_metadata(skill_dir, skill_md)
        except Exception:
            logger.exception("Failed to load SKILL.md metadata from %s", skill_md)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_module_path(py_file: Path) -> Optional[str]:
        """Convert a .py file path to a dotted module path.

        Walks up from the file until it finds a directory without __init__.py
        (the package root).  Falls back to searching for ``raavan``
        in the path parts.
        """
        parts = py_file.resolve().parts
        # Find the *last* 'raavan' in the path to anchor at src/raavan (not the
        # repo root directory which happens to share the same name on some layouts).
        idx: Optional[int] = None
        for i, part in enumerate(parts):
            if part == "raavan":
                idx = i

        if idx is None:
            logger.warning(
                "Cannot determine module path for %s — 'raavan' not found in path",
                py_file,
            )
            return None

        # Build dotted path from raavan/... down to filename (sans .py)
        module_parts = list(parts[idx:])
        # Remove .py extension from last part
        module_parts[-1] = module_parts[-1].replace(".py", "")
        return ".".join(module_parts)
