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

from substrate.agents.storage.tasks import current_thread_id, current_user_id
from substrate.capabilities.tools.code_interpreter.code_interpreter.tool import (
    session_dir,
)
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

    def __init__(self, skill_manager: Any, *, file_store: Any = None) -> None:
        self._manager = skill_manager
        # Used to stage an activated skill's scripts/*.py into the caller's
        # own sandbox session, so the model imports a real, guaranteed-correct
        # function instead of retyping one from the SKILL.md example each
        # time (see excel_report/scripts/substrate_excel_charts.py's
        # docstring for the exact bug this fixes). Optional: without a
        # file_store, activation still works — it just skips staging, same
        # as before this existed.
        self._file_store = file_store

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
            return await self._activate(name)
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

    async def _activate(self, name: str) -> ToolExecutionResult:
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
        await self._stage_scripts(skill)
        return ToolExecutionResult(
            content=[TextBlock(text=skill.body)],
            structured_content={
                "skill_name": skill.name,
                "skill_version": skill.metadata.version,
            },
        )

    async def _stage_scripts(self, skill: Any) -> None:
        """Copy this skill's scripts/*.py into the caller's own sandbox
        session (under scripts/) via the injected file_store, so
        code_interpreter can `sys.path.insert(0, "scripts"); import <name>`
        instead of the model retyping the code from SKILL.md. Same
        session-key convention as CodeInterpreterTool, and the same
        file_store abstraction the S3-backed sandbox staging already uses
        (see runtimes/staged.py) — so this works whether the sandbox
        workspace is a local dir or gets materialised from object storage
        per run, not just the local-filesystem case.
        """
        scripts = getattr(skill, "scripts", None)
        if not scripts or self._file_store is None:
            return
        user_id = current_user_id.get()
        thread_id = current_thread_id.get()
        if not thread_id:
            return
        prefix = session_dir(user_id, thread_id)
        for script_path in scripts:
            try:
                data = script_path.read_bytes()
                await self._file_store.upload(
                    f"{prefix}/scripts/{script_path.name}",
                    data,
                    content_type="text/x-python",
                )
            except Exception as exc:  # noqa: BLE001 - never fail activation over staging
                logger.warning(
                    "Failed to stage script %s for skill %s: %s",
                    script_path.name,
                    skill.name,
                    exc,
                )
