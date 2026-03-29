"""Tests for CapabilityRegistry — registration, search, browse, alias resolution."""

from __future__ import annotations

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk
from raavan.core.tools.catalog import CapabilityRegistry


# ---------------------------------------------------------------------------
# Helpers — minimal tool stubs
# ---------------------------------------------------------------------------


class _StubTool(BaseTool):
    """Minimal tool for testing."""

    def __init__(
        self,
        name: str = "stub",
        description: str = "A stub tool",
        **kwargs,
    ) -> None:
        super().__init__(name=name, description=description, **kwargs)

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": "ok"}])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_tool_basic():
    cat = CapabilityRegistry()
    tool = _StubTool(name="calc", description="Calculator")
    cat.register_tool(tool, category="productivity", tags=["math"])

    assert "calc" in cat
    assert cat.get("calc") is not None
    assert cat.get("calc").kind == "tool"
    assert cat.get("calc").category == "productivity"


def test_register_tool_inherits_metadata():
    """Tool metadata (category/tags/aliases) set on BaseTool.__init__ is inherited."""
    tool = _StubTool(
        name="t1",
        description="test",
        category="research",
        tags=["web", "browse"],
        aliases=["browser"],
    )
    cat = CapabilityRegistry()
    cat.register_tool(tool)

    entry = cat.get("t1")
    assert entry is not None
    assert entry.category == "research"
    assert "web" in entry.tags
    assert "browse" in entry.tags


def test_register_tool_override_metadata():
    """Registration-level metadata overrides tool-level."""
    tool = _StubTool(name="t2", description="test", category="research")
    cat = CapabilityRegistry()
    cat.register_tool(tool, category="media", tags=["music"])

    entry = cat.get("t2")
    assert entry.category == "media"
    assert "music" in entry.tags


def test_register_skill():
    """Skills can be registered with metadata."""

    class FakeMetadata:
        name = "web-research"
        description = "Research the web"
        category = "research"
        tags = ["web", "search"]
        aliases = ["internet-research"]

    cat = CapabilityRegistry()
    cat.register_skill(FakeMetadata())

    entry = cat.get("web-research")
    assert entry is not None
    assert entry.kind == "skill"
    assert entry.category == "research"


def test_unregister():
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="x", description="x"))
    assert "x" in cat
    cat.unregister("x")
    assert "x" not in cat


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


def test_alias_resolution():
    cat = CapabilityRegistry()
    cat.register_tool(
        _StubTool(name="spotify_player", description="Play music"),
        aliases=["music_player", "play_music"],
    )

    # Get by canonical name
    entry = cat.get("spotify_player")
    assert entry is not None
    assert entry.name == "spotify_player"

    # Get by alias
    entry2 = cat.get("music_player")
    assert entry2 is not None
    assert entry2.name == "spotify_player"

    # get_tool by alias
    tool = cat.get_tool("play_music")
    assert tool is not None
    assert tool.name == "spotify_player"


def test_alias_not_found():
    cat = CapabilityRegistry()
    assert cat.get("nonexistent") is None
    assert cat.get_tool("nonexistent") is None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_by_name():
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="calculator", description="Math operations"))
    cat.register_tool(_StubTool(name="web_surfer", description="Browse the web"))

    results = cat.search("calculator")
    assert len(results) >= 1
    assert results[0].name == "calculator"


def test_search_by_tags():
    cat = CapabilityRegistry()
    cat.register_tool(
        _StubTool(name="spotify_player", description="Control Spotify"),
        tags=["music", "play", "song"],
    )
    cat.register_tool(
        _StubTool(name="calculator", description="Math"),
        tags=["math", "calculate"],
    )

    results = cat.search("play music")
    assert len(results) >= 1
    assert results[0].name == "spotify_player"


def test_search_by_alias():
    cat = CapabilityRegistry()
    cat.register_tool(
        _StubTool(name="code_interpreter", description="Execute code"),
        aliases=["sandbox", "code_exec"],
    )

    results = cat.search("sandbox")
    assert len(results) >= 1
    assert results[0].name == "code_interpreter"


def test_search_kind_filter():
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="web_surfer", description="Browse"))

    class FakeMeta:
        name = "web-research"
        description = "Research"

    cat.register_skill(FakeMeta())

    # Only tools
    tools_only = cat.search("web", kind_filter="tool")
    assert all(e.kind == "tool" for e in tools_only)

    # Only skills
    skills_only = cat.search("web", kind_filter="skill")
    assert all(e.kind == "skill" for e in skills_only)


def test_search_excludes():
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="capability_search", description="Search tools"))
    cat.register_tool(_StubTool(name="calculator", description="Math search"))

    results = cat.search("search", exclude_names={"capability_search"})
    names = [e.name for e in results]
    assert "capability_search" not in names


def test_search_empty_query():
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="x", description="x"))
    assert cat.search("") == []
    assert cat.search("   ") == []


def test_wrong_parent_still_found():
    """A tool miscategorized should still be found via tags."""
    cat = CapabilityRegistry()
    # spotify_player mistakenly placed under development instead of media
    cat.register_tool(
        _StubTool(name="spotify_player", description="Control Spotify"),
        category="development",
        tags=["music", "play", "song", "spotify"],
    )

    results = cat.search("play music")
    assert len(results) >= 1
    assert results[0].name == "spotify_player"


# ---------------------------------------------------------------------------
# Browse & Categories
# ---------------------------------------------------------------------------


def test_browse_category():
    cat = CapabilityRegistry()
    cat.register_tool(
        _StubTool(name="calc", description="Math"),
        category="productivity",
    )
    cat.register_tool(
        _StubTool(name="clock", description="Time"),
        category="productivity",
    )
    cat.register_tool(
        _StubTool(name="browser", description="Web"),
        category="research",
    )

    prod_items = cat.browse("productivity")
    assert len(prod_items) == 2
    assert {e.name for e in prod_items} == {"calc", "clock"}


def test_list_top_categories():
    cat = CapabilityRegistry()
    top_cats = cat.list_categories()
    top_paths = [c.path for c in top_cats]
    assert "system" in top_paths
    assert "research" in top_paths
    assert "productivity" in top_paths


def test_list_subcategories():
    cat = CapabilityRegistry()
    data_subs = cat.list_categories("data")
    sub_paths = [c.path for c in data_subs]
    assert "data/visualization" in sub_paths
    assert "data/management" in sub_paths


def test_get_category():
    cat = CapabilityRegistry()
    node = cat.get_category("data/visualization")
    assert node is not None
    assert node.description != ""

    assert cat.get_category("nonexistent/path") is None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_all_tools():
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="a", description="a"))
    cat.register_tool(_StubTool(name="b", description="b"))

    tools = cat.all_tools()
    assert len(tools) == 2
    assert all(isinstance(t, BaseTool) for t in tools)


def test_by_risk():
    cat = CapabilityRegistry()
    cat.register_tool(
        _StubTool(name="safe_tool", description="safe", risk=ToolRisk.SAFE)
    )
    cat.register_tool(
        _StubTool(name="critical_tool", description="critical", risk=ToolRisk.CRITICAL)
    )

    safe = cat.by_risk(ToolRisk.SAFE)
    assert len(safe) == 1
    assert safe[0].name == "safe_tool"


def test_insertion_order():
    cat = CapabilityRegistry()
    for name in ["c", "a", "b"]:
        cat.register_tool(_StubTool(name=name, description=name))

    assert cat.names() == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# Dunder protocols
# ---------------------------------------------------------------------------


def test_len():
    cat = CapabilityRegistry()
    assert len(cat) == 0
    cat.register_tool(_StubTool(name="x", description="x"))
    assert len(cat) == 1


def test_contains():
    cat = CapabilityRegistry()
    cat.register_tool(
        _StubTool(name="foo", description="foo"),
        aliases=["bar"],
    )
    assert "foo" in cat
    assert "bar" in cat
    assert "baz" not in cat


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_lifecycle():
    """startup/shutdown should call tool methods without error."""
    cat = CapabilityRegistry()
    cat.register_tool(_StubTool(name="x", description="x"))
    await cat.startup()
    await cat.shutdown()
