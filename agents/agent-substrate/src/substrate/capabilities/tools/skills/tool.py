"""SkillTool — LLM-callable interface for the agent skills system.

Three actions:
- ``list``           → returns all discovered skills with names and brief descriptions
- ``activate``        → loads the full SKILL.md body (+ names of any
                        scripts/references files) for a named skill on demand
- ``read_reference``  → reads one reference file's content by name, for a
                        skill that keeps detail out of its main body
                        (see excel_report/SKILL.md's Step 2 for why)

Skills roster (names + descriptions) is injected into the system prompt at
startup via SkillManager.system_prompt_suffix(); this tool lets the LLM read
the full content of any skill when it decides to use one.
"""

from __future__ import annotations

from typing import Any

from substrate.kernel import TextBlock
from substrate.kernel.tools import ToolExecutionResult, ToolType
from substrate.logger import setup_logging

logger = setup_logging()


class SkillTool:
    """Discover and activate agent skills."""

    tool_type: str = ToolType.SKILL
    name: str = "skills"
    description: str = (
        "Manage agent skills. "
        "action=list: show all available skills with names and descriptions. "
        "action=activate: load the full instructions for a named skill so you can follow them "
        "— the response also lists any scripts/references files the skill has. "
        "action=read_reference: read one reference file's content by name (from the list "
        "activate returned) when a skill points you at one for extra detail."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "activate", "read_reference"],
                "description": (
                    "list — show available skill names and descriptions; "
                    "activate — load full SKILL.md content for a skill; "
                    "read_reference — read one of that skill's reference files."
                ),
            },
            "name": {
                "type": "string",
                "description": "Skill name (required for activate and read_reference).",
            },
            "file": {
                "type": "string",
                "description": "Reference filename to read (required for read_reference).",
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
        file: str = "",
        **_: object,
    ) -> ToolExecutionResult:
        if action == "list":
            return self._list()
        if action == "activate":
            return self._activate(name)
        if action == "read_reference":
            return self._read_reference(name, file)
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
        # to_context_block() appends "## Available Scripts"/"## Reference
        # Files" filename lists after the body — without this, a skill
        # pointing at references/foo.md (to keep its own body short) would
        # be a dead pointer: nothing else surfaces those filenames to you.
        return ToolExecutionResult(
            content=[TextBlock(text=skill.to_context_block())],
            structured_content={
                "skill_name": skill.name,
                "skill_version": skill.metadata.version,
            },
        )

    def _read_reference(self, name: str, file: str) -> ToolExecutionResult:
        if not name.strip() or not file.strip():
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text="'name' and 'file' are both required for read_reference."
                    )
                ],
                is_error=True,
            )
        skill = self._manager.activate(name)
        if skill is None:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Skill '{name}' not found.")],
                is_error=True,
            )
        content = skill.read_reference(file)
        if content is None:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=f"Reference '{file}' not found on skill '{name}'. "
                        f"Available: {', '.join(skill.list_references()) or '(none)'}"
                    )
                ],
                is_error=True,
            )
        return ToolExecutionResult(content=[TextBlock(text=content)])
