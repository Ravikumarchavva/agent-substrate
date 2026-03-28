"""
Agent Skills - Skill Manager

Coordinates skill discovery, system-prompt injection, and active skill tracking.

Integration pattern (agentskills.io spec):
1. At startup: scan dirs → build <available_skills> XML block → inject into system prompt
2. At activation: model references SKILL.md location → manager loads full body → inject into context
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .loader import SkillLoader
from .models import Skill, SkillMetadata

logger = logging.getLogger(__name__)


class SkillManager:
    """
    Top-level manager for Agent Skills.

    1. Discovers skills from configured directories.
    2. Produces the <available_skills> XML snippet for system-prompt injection.
    3. Activates skills on-demand (lazy load full SKILL.md content).
    4. Provides context blocks for active skills.

    Example:
        manager = SkillManager(skill_dirs=["./skills", "~/.claude/skills"])
        manager.discover()

        # Inject into system prompt:
        system_prompt = base_instructions + "\\n" + manager.available_skills_xml()

        # When model wants to use a skill:
        skill = manager.activate("spotify-player")
        context = manager.active_context_block()
    """

    def __init__(
        self,
        skill_dirs: Optional[List[str | Path]] = None,
        auto_discover: bool = True,
    ) -> None:
        self._loader = SkillLoader(skill_dirs=skill_dirs or [])
        self._active: Dict[str, Skill] = {}  # name -> fully loaded Skill
        self._discovered: List[SkillMetadata] = []

        if auto_discover:
            self.discover()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> List[SkillMetadata]:
        """
        Scan skill directories and populate the skill index.
        Safe to call multiple times (re-discovery clears cache).
        """
        self._active.clear()
        self._discovered = self._loader.discover_all()
        if self._discovered:
            names = [m.name for m in self._discovered]
            logger.info("Skills discovered: %s", names)
        else:
            logger.debug("No skills found in configured directories.")
        return self._discovered

    @property
    def skill_count(self) -> int:
        return len(self._discovered)

    @property
    def available_names(self) -> List[str]:
        return [m.name for m in self._discovered]

    # ------------------------------------------------------------------
    # System-prompt injection
    # ------------------------------------------------------------------

    def available_skills_xml(self) -> str:
        """
        Build the <available_skills> XML block injected at the END of the
        system prompt.  Only metadata is included (progressive disclosure).

        Format (per agentskills.io spec):
            <available_skills>
              <skill>
                <name>skill-name</name>
                <description>...</description>
                <location>/abs/path/to/skill-name/SKILL.md</location>
              </skill>
              ...
            </available_skills>

        Returns empty string if no skills are configured.
        """
        if not self._discovered:
            return ""

        lines = ["<available_skills>"]
        for meta in self._discovered:
            location = str(meta.skill_md_path).replace("\\", "/")
            lines += [
                "  <skill>",
                f"    <name>{_xml_escape(meta.name)}</name>",
                f"    <description>{_xml_escape(meta.description)}</description>",
                f"    <location>{_xml_escape(location)}</location>",
                "  </skill>",
            ]
        lines.append("</available_skills>")
        return "\n".join(lines)

    def system_prompt_suffix(self) -> str:
        """
        Returns the full text to append to the system prompt.
        Includes a brief directive plus the <available_skills> block.
        """
        xml = self.available_skills_xml()
        if not xml:
            return ""
        return (
            "\n\nYou have access to the following skills. "
            "When a task matches a skill's purpose, read the full SKILL.md "
            "at the listed location and follow its instructions precisely.\n\n" + xml
        )

    def inject_into_prompt(self, system_prompt: str) -> str:
        """Append skill context to an existing system prompt string."""
        suffix = self.system_prompt_suffix()
        if not suffix:
            return system_prompt
        return system_prompt.rstrip() + "\n" + suffix

    # ------------------------------------------------------------------
    # Activation (lazy full-load)
    # ------------------------------------------------------------------

    def activate(self, name: str) -> Optional[Skill]:
        """
        Activate a skill by name (loads full SKILL.md body lazily).
        Returns None if the skill is not found.
        """
        if name in self._active:
            return self._active[name]

        skill = self._loader.load_skill(name)
        if skill is None:
            logger.warning("Skill %r not found; cannot activate.", name)
            return None

        self._active[name] = skill
        logger.info("Activated skill: %r", name)
        return skill

    def activate_by_path(self, skill_md_path: str | Path) -> Optional[Skill]:
        """
        Activate a skill by its SKILL.md file path (as the model might reference).
        """
        p = Path(skill_md_path).expanduser().resolve()
        skill_dir = p.parent

        skill = self._loader.load_skill_by_path(skill_dir)
        if skill is None:
            return None

        self._active[skill.name] = skill
        return skill

    def deactivate(self, name: str) -> None:
        """Remove a skill from the active set."""
        self._active.pop(name, None)

    def deactivate_all(self) -> None:
        """Clear all active skills (e.g., between conversations)."""
        self._active.clear()

    # ------------------------------------------------------------------
    # Context for active skills
    # ------------------------------------------------------------------

    def active_context_block(self) -> str:
        """
        Returns combined context for all currently active skills.
        Intended to be injected into the user/assistant context turn.
        Returns empty string if no skills are active.
        """
        if not self._active:
            return ""

        parts = ["<active_skills>"]
        for skill in self._active.values():
            parts += [
                f"  <skill name={skill.name!r}>",
                skill.to_context_block().replace("  ", "    "),  # indent
                "  </skill>",
            ]
        parts.append("</active_skills>")
        return "\n".join(parts)

    def get_skill(self, name: str) -> Optional[Skill]:
        """Return an active skill by name; None if not activated."""
        return self._active.get(name)

    def get_metadata(self, name: str) -> Optional[SkillMetadata]:
        """Return metadata for any discovered skill (even if not activated)."""
        return self._loader.get_metadata(name)

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> List[Dict[str, Any]]:
        """Return serialisable list of discovered skill metadata."""
        result = []
        for meta in self._discovered:
            result.append(
                {
                    "name": meta.name,
                    "description": meta.description,
                    "version": meta.version,
                    "license": meta.license,
                    "allowed_tools": meta.allowed_tools,
                    "active": meta.name in self._active,
                    "path": str(meta.path),
                }
            )
        return result


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _xml_escape(text: str) -> str:
    """Minimal XML escaping for values embedded in skill XML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
