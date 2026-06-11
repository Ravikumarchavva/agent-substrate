"""SkillTool — LLM-callable interface for the agent skills system.

Two actions:
- ``list``     → returns all discovered skills with names and brief descriptions
- ``activate`` → loads the full SKILL.md body for a named skill on demand

Skills roster (names + descriptions) is injected into the system prompt at
startup via SkillManager.system_prompt_suffix(); this tool lets the LLM read
the full content of any skill when it decides to use one.
"""

from __future__ import annotations

from typing import Any

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult, ToolType
from ravi.logger import setup_logging

logger = setup_logging()


class SkillTool:
    """Discover and activate agent skills."""

    tool_type: str = ToolType.SKILL
    name: str = "skills"
    description: str = (
        "Manage agent skills. "
        "action=list: show all available skills with names and descriptions. "
        "action=activate: load the full instructions for a named skill so you can follow them."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "activate"],
                "description": (
                    "list — show available skill names and descriptions; "
                    "activate — load full SKILL.md content for a skill."
                ),
            },
            "name": {
                "type": "string",
                "description": "Skill name to activate (required for activate action).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, skill_manager: Any) -> None:
        self._manager = skill_manager

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        name: str = "",
        **_: object,
    ) -> ToolExecutionResult:
        if action == "list":
            return self._list()
        if action == "activate":
            return self._activate(name)
        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )

    def _list(self) -> ToolExecutionResult:
        metadatas = (
            self._manager._loader.all_metadata()
            if hasattr(self._manager, "_loader")
            else []
        )
        if not metadatas:
            return ToolExecutionResult(content=[TextBlock(text="No skills available.")])
        lines = [f"Available skills ({len(metadatas)}):"]
        for meta in metadatas:
            lines.append(f"  {meta.name}: {meta.description}")
        return ToolExecutionResult(content=[TextBlock(text="\n".join(lines))])

    def _activate(self, name: str) -> ToolExecutionResult:
        if not name.strip():
            return ToolExecutionResult(
                content=[TextBlock(text="'name' is required for activate.")],
                is_error=True,
            )
        skill = self._manager.activate(name)
        if skill is None:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Skill '{name}' not found.")],
                is_error=True,
            )
        return ToolExecutionResult(
            content=[TextBlock(text=skill.body)],
            app_data={
                "skill_name": skill.name,
                "skill_version": skill.metadata.version,
            },
        )
