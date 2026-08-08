"""init_tool_registry — the `skills` tool must actually reach the registry.

Before this, SkillManager discovered SKILL.md packages on disk (auto_discover=
True at construction) but nothing ever registered SkillTool into the Toolbox
or appended the discovered-skills roster to the system prompt — a skill
existing on disk did nothing, since the model had no tool to list or activate
one and no reason to know any existed. Pins both halves of that wiring.
"""

from __future__ import annotations

from substrate.capabilities.tools.skills._manager import SkillManager
from substrate.config import SubstrateConfig
from substrate.infrastructure.serving_factory import init_tool_registry


async def test_skills_tool_registered_when_skill_manager_given():
    manager = SkillManager(auto_discover=True)
    result = await init_tool_registry(
        SubstrateConfig(),
        session_factory=None,
        bridge_registry=None,
        skill_manager=manager,
    )

    assert result.registry.get("skills") is not None


async def test_skills_tool_absent_without_a_skill_manager():
    """Default (no skill_manager passed) must not register a tool bound to
    nothing — callers that don't wire skills shouldn't get a dead tool."""
    result = await init_tool_registry(
        SubstrateConfig(), session_factory=None, bridge_registry=None
    )

    assert result.registry.get("skills") is None


async def test_excel_report_skill_is_discovered_and_activatable():
    """The skill this wiring exists to serve — pins that it's actually found
    on disk and its full body loads, not just that the file parses."""
    manager = SkillManager(auto_discover=True)

    assert "excel-report" in manager.available_names

    skill = manager.activate("excel-report")

    assert skill is not None
    assert "openpyxl" in skill.body
    assert "chart" in skill.body.lower()
