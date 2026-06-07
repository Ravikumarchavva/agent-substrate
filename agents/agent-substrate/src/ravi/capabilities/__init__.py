"""capabilities — unified capability system for tools, skills, connectors, and pipelines.

Directory layout::

    capabilities/
    ├── tools/        ← Tool implementations grouped by domain (web/, files/, ai/, …)
    ├── skills/       ← SKILL.md prompt-skill packages + skill system machinery
    ├── connectors/   ← Stateful external service clients (email, postgres_query, …)
    ├── triggers/     ← Trigger monitors (scheduled, webhooks, events)
    ├── knowledge/    ← RAG pipeline, vector store, chunkers, loaders, reranker
    ├── pipeline/     ← Declarative pipeline execution engine and store
    └── discovery.py  ← CapabilityDiscovery — startup-only filesystem scanner
"""

from __future__ import annotations

from ravi.capabilities.pipeline.chain import ChainRuntime
from ravi.capabilities.pipeline.data_ref import DataRef, DataRefStore
from ravi.capabilities.pipeline.engine import PipelineDef, PipelineEngine, PipelineResult
from ravi.capabilities.pipeline.store import PipelineStore
from ravi.capabilities.discovery import CatalogPackage, CapabilityDiscovery
from ravi.capabilities.skills._manager import SkillManager
from ravi.capabilities.skills._loader import SkillLoader
from ravi.capabilities.skills._models import SkillPackage, SkillMetadata

# Backward-compat alias
CatalogScanner = CapabilityDiscovery

__all__ = [
    "CatalogPackage",
    "CatalogScanner",
    "CapabilityDiscovery",
    "ChainRuntime",
    "DataRef",
    "DataRefStore",
    "PipelineDef",
    "PipelineEngine",
    "PipelineResult",
    "PipelineStore",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
    "SkillPackage",
]
