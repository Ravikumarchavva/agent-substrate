"""capabilities — unified capability system for tools, skills, connectors, and pipelines.

Directory layout::

    capabilities/
    ├── tools/        ← Tool implementations (human_input, web_surfer, task_manager, …)
    ├── skills/       ← SKILL.md prompt-skill packages (debugging, code_review, …)
    ├── connectors/   ← External service connectors (email, postgres_query, …)
    ├── triggers/     ← Trigger monitors (scheduled, webhooks, events)
    ├── knowledge/    ← RAG pipeline, vector store, chunkers, loaders, reranker
    └── internal/     ← Scanners, skill loader/manager, pipeline engine, chain runtime
"""

from __future__ import annotations

from ravi.capabilities.internal.chain_runtime import ChainRuntime
from ravi.capabilities.internal.data_ref import DataRef, DataRefStore
from ravi.capabilities.internal.pipeline import (
    PipelineDef,
    PipelineEngine,
    PipelineStore,
)
from ravi.capabilities.internal.scanner import CatalogPackage, CatalogScanner
from ravi.capabilities.internal.skill_manager import SkillManager
from ravi.capabilities.internal.skill_loader import SkillLoader
from ravi.capabilities.internal.skill_models import SkillPackage, SkillMetadata

__all__ = [
    "CatalogPackage",
    "CatalogScanner",
    "ChainRuntime",
    "DataRef",
    "DataRefStore",
    "PipelineDef",
    "PipelineEngine",
    "PipelineStore",
    "SkillPackage",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
]
