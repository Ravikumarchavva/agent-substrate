"""CapabilityDiscovery — startup-only filesystem scanner.

Walks the capabilities directories looking for packages that follow the
naming convention:
- ``tool.py``       → tool component (imports first Tool-protocol class)
- ``SKILL.md``      → skill component (parsed via SkillLoader._load_metadata)
- ``connector.py``  → connector component (imports first *Connector class)

Run once at app boot to populate the Toolbox; not LLM-callable.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Set, Type

from ravi.logger import setup_logging

logger = setup_logging()

ComponentKind = Literal["tool", "skill", "connector", "pipeline_step"]


@dataclass
class CatalogPackage:
    """A discovered capability package and its detected components."""

    name: str
    path: Path
    components: Set[ComponentKind] = field(default_factory=set)
    tool_class: Type[Any] | None = field(default=None, repr=False)
    skill_metadata: Any | None = field(default=None, repr=False)
    connector_class: Type[Any] | None = field(default=None, repr=False)
    config: Dict[str, Any] | None = field(default=None, repr=False)


def _default_capability_dirs() -> List[Path]:
    """Return the built-in capability type subdirectories."""
    tools_root = Path(__file__).resolve().parent  # capabilities/tools/
    return [
        tools_root,  # tool packages (task_manager, code_interpreter, …)
        tools_root / "skills",  # SKILL.md packages
        tools_root / "connectors",  # connector packages
    ]


class CapabilityDiscovery:
    """Discover capability packages by filesystem convention.

    Parameters
    ----------
    capability_dirs
        Directories to scan. Defaults to capabilities/tools, skills, connectors.
    """

    def __init__(
        self,
        capability_dirs: List[str | Path] | None = None,
    ) -> None:
        self._dirs: List[Path] = []
        configured = (
            _default_capability_dirs() if capability_dirs is None else capability_dirs
        )
        for d in configured:
            p = Path(d).expanduser().resolve()
            if p.is_dir():
                self._dirs.append(p)
            else:
                logger.debug("Capability directory not found (skipping): %s", p)

        self._packages: Dict[str, CatalogPackage] = {}

    def discover(self) -> List[CatalogPackage]:
        """Scan all configured directories for capability packages.

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
                if child.name.startswith("_") or child.name == "__pycache__":
                    continue

                pkg = self._scan_package(child)
                if pkg is None or not pkg.components:
                    continue

                if pkg.name in found:
                    logger.debug(
                        "Capability package %r already discovered — skipping %s",
                        pkg.name,
                        child,
                    )
                else:
                    found[pkg.name] = pkg
                    logger.debug(
                        "Discovered capability package: %r (%s) at %s",
                        pkg.name,
                        ", ".join(sorted(pkg.components)),
                        child,
                    )

        self._packages = found
        return list(found.values())

    def get(self, name: str) -> CatalogPackage | None:
        """Return a discovered package by name."""
        return self._packages.get(name)

    def all(self) -> List[CatalogPackage]:
        """Return all discovered packages."""
        return list(self._packages.values())

    def _scan_package(self, pkg_dir: Path) -> CatalogPackage | None:
        """Detect components in a single capability directory."""
        name = pkg_dir.name
        pkg = CatalogPackage(name=name, path=pkg_dir)

        tool_py = pkg_dir / "tool.py"
        if tool_py.is_file():
            tool_cls = self._load_tool_class(pkg_dir, tool_py)
            if tool_cls is not None:
                pkg.components.add("tool")
                pkg.tool_class = tool_cls

        skill_md = pkg_dir / "SKILL.md"
        if skill_md.is_file():
            meta = self._load_skill_metadata(pkg_dir, skill_md)
            if meta is not None:
                pkg.components.add("skill")
                pkg.skill_metadata = meta

        connector_py = pkg_dir / "connector.py"
        if connector_py.is_file():
            connector_cls = self._load_connector_class(pkg_dir, connector_py)
            if connector_cls is not None:
                pkg.components.add("connector")
                pkg.connector_class = connector_cls

        pipeline_py = pkg_dir / "pipeline.py"
        if pipeline_py.is_file():
            pkg.components.add("pipeline_step")

        return pkg

    def _load_tool_class(self, pkg_dir: Path, tool_py: Path) -> Type[Any] | None:
        """Import tool.py and return the first class satisfying the Tool protocol."""
        module_path = self._to_module_path(tool_py)
        if module_path is None:
            return None
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            logger.exception("Failed to import tool module: %s", module_path)
            return None

        _TOOL_ATTRS = {"name", "description", "input_schema", "execute"}
        for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                not inspect.isabstract(obj)
                and obj.__module__ == mod.__name__
                and _TOOL_ATTRS.issubset(dir(obj))
            ):
                return obj
        logger.warning("No Tool-protocol class found in %s", tool_py)
        return None

    def _load_connector_class(
        self, pkg_dir: Path, connector_py: Path
    ) -> Type[Any] | None:
        """Import connector.py and return the first *Connector class found."""
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

    def _load_skill_metadata(self, skill_dir: Path, skill_md: Path) -> Any | None:
        """Parse SKILL.md using the SkillLoader helper."""
        try:
            from ravi.capabilities.tools.skills._loader import SkillLoader

            loader = SkillLoader.__new__(SkillLoader)
            return loader._load_metadata(skill_dir, skill_md)
        except Exception:
            logger.exception("Failed to load SKILL.md metadata from %s", skill_md)
            return None

    @staticmethod
    def _to_module_path(py_file: Path) -> str | None:
        """Convert a .py file path to a dotted module path."""
        parts = py_file.resolve().parts
        idx: int | None = None
        for i, part in enumerate(parts):
            if part == "ravi":
                idx = i

        if idx is None:
            logger.warning(
                "Cannot determine module path for %s — 'ravi' not found in path",
                py_file,
            )
            return None

        module_parts = list(parts[idx:])
        module_parts[-1] = module_parts[-1].replace(".py", "")
        return ".".join(module_parts)


# Backward-compat alias used in existing imports (removed after migration)
CatalogScanner = CapabilityDiscovery
