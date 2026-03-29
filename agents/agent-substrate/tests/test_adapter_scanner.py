"""Tests for CatalogScanner — convention-based adapter discovery."""

from __future__ import annotations

from pathlib import Path

from raavan.catalog._scanner import CatalogScanner


class TestCatalogScanner:
    """CatalogScanner discovery tests."""

    def test_discover_real_adapters(self) -> None:
        """The built-in adapters directory should discover adapter packages."""
        scanner = CatalogScanner()
        packages = scanner.discover()

        assert len(packages) > 0
        names = {p.name for p in packages}
        # These should exist from Phase 3 — tools copied to adapters/
        assert "task_manager" in names
        assert "human_input" in names
        assert "capability_search" in names

    def test_tools_have_tool_component(self) -> None:
        """Adapters with tool.py should have 'tool' in their components."""
        scanner = CatalogScanner()
        packages = scanner.discover()

        tools_with_tool_py = [p for p in packages if (p.path / "tool.py").is_file()]
        assert len(tools_with_tool_py) > 0
        for pkg in tools_with_tool_py:
            assert "tool" in pkg.components, f"{pkg.name} should have 'tool' component"

    def test_skills_have_skill_component(self) -> None:
        """Adapters with SKILL.md should have 'skill' in their components."""
        scanner = CatalogScanner()
        packages = scanner.discover()

        skills = [p for p in packages if (p.path / "SKILL.md").is_file()]
        assert len(skills) > 0
        for pkg in skills:
            assert "skill" in pkg.components, (
                f"{pkg.name} should have 'skill' component"
            )

    def test_connectors_have_connector_component(self) -> None:
        """Adapters with connector.py should have 'connector' in their components."""
        scanner = CatalogScanner()
        packages = scanner.discover()

        connectors = [p for p in packages if (p.path / "connector.py").is_file()]
        assert len(connectors) > 0
        for pkg in connectors:
            assert "connector" in pkg.components, (
                f"{pkg.name} should have 'connector' component"
            )

    def test_private_dirs_skipped(self) -> None:
        """Directories starting with _ should be skipped."""
        scanner = CatalogScanner()
        packages = scanner.discover()

        names = {p.name for p in packages}
        assert "_scanner" not in names
        assert "_data_ref" not in names
        assert "_chain_runtime" not in names
        assert "_temporal" not in names
        assert "_triggers" not in names
        assert "__pycache__" not in names

    def test_get_by_name(self) -> None:
        """Can retrieve a specific package by name after discovery."""
        scanner = CatalogScanner()
        scanner.discover()

        pkg = scanner.get("task_manager")
        assert pkg is not None
        assert pkg.name == "task_manager"
        assert "tool" in pkg.components

    def test_get_nonexistent_returns_none(self) -> None:
        """Getting a non-existent package returns None."""
        scanner = CatalogScanner()
        scanner.discover()

        assert scanner.get("this_does_not_exist") is None

    def test_empty_dir_not_discovered(self, tmp_path: Path) -> None:
        """Directories without convention files are skipped."""
        empty = tmp_path / "empty_pkg"
        empty.mkdir()

        scanner = CatalogScanner(adapter_dirs=[tmp_path])
        packages = scanner.discover()

        names = {p.name for p in packages}
        assert "empty_pkg" not in names

    def test_all_returns_same_as_discover(self) -> None:
        """all() returns the same packages as discover()."""
        scanner = CatalogScanner()
        discovered = scanner.discover()
        assert scanner.all() == discovered
