"""
Agent Skills
============
Implements the Agent Skills open standard (https://agentskills.io).

Quick start:
    from agent_framework.skills import SkillManager

    manager = SkillManager(skill_dirs=["./skills"])
    system_prompt = manager.inject_into_prompt(base_prompt)
"""

from .loader import SkillLoader
from .manager import SkillManager
from .models import Skill, SkillMetadata

__all__ = [
    "Skill",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
]
