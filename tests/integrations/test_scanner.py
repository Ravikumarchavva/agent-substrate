from __future__ import annotations

from substrate.capabilities.tools.discovery import CapabilityDiscovery


def test_catalog_scanner_discovery(tmp_path, monkeypatch):
    # Setup mock tools folder structure in tmp_path
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    mock_tool_dir = tools_dir / "my_mock_tool"
    mock_tool_dir.mkdir()

    # Write a mock tool.py so it is detected as a file
    tool_py = mock_tool_dir / "tool.py"
    tool_py.write_text("dummy")

    class MyMockTool:
        name = "my_mock_tool"
        description = "Mock tool for catalog scanner test."
        input_schema = {"type": "object", "properties": {}}

    # Monkeypatch _load_tool_class to return MyMockTool directly
    monkeypatch.setattr(CapabilityDiscovery, "_load_tool_class", lambda *a: MyMockTool)

    # Initialize scanner with the temp directory as a scanned path
    scanner = CapabilityDiscovery(capability_dirs=[tools_dir])
    packages = scanner.discover()

    assert len(packages) == 1
    pkg = packages[0]
    assert pkg.name == "my_mock_tool"
    assert "tool" in pkg.components
    assert pkg.tool_class is MyMockTool
