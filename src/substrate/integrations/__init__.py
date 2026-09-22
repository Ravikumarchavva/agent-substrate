"""substrate.integrations — everything that reaches outside this process (L2).

Production infrastructure swap-ins for kernel Protocols ``agents/`` already
implements a zero-infra default of (Postgres/S3/Redis/pgvector/AGE storage
backends, additional LLM vendor clients), plus genuinely new,
externally-backed capabilities with no L1 equivalent (tools that call real
APIs, RAG, MCP, the sandboxed code interpreter).

Directory layout::

    integrations/
    ├── llm/          ← vendor auto-detection factory + anthropic/, gemini/, openai/, encoders/
    ├── tools/        ← all tool implementations + skills + MCP + discovery
    │   ├── mcp/      ← Model Context Protocol client/tool bridge
    │   ├── skills/   ← SKILL.md prompt-skill packages
    │   ├── chain/    ← sandboxed code-mode tool chaining (bridge, prelude, ToolChainTool)
    │   └── web/, files/, ai/, compute/, utils/, communication/, database/, …
    ├── knowledge/    ← RAG pipeline, chunkers, loaders, reranker
    ├── pipeline/     ← declarative pipeline execution engine + DataRefStore/ArtifactStore
    ├── memory/       ← Postgres/Redis/Lance MemoryStore implementations
    ├── history/      ← Postgres HistoryProvider implementation
    ├── vector/       ← Postgres/LanceDB VectorStore implementations
    ├── graph/        ← Apache AGE/LanceDB GraphStore implementations
    ├── storage/      ← S3FileStore, PostgresWorkspaceStore
    ├── safety/       ← TextSafetyClassifier, ImageSafetyClassifier
    ├── artifacts/    ← OKF artifact store
    ├── gdpr/         ← cross-store tenant erasure
    ├── triggers/     ← trigger monitors (scheduled, webhooks, events)
    ├── events/       ← Redis-backed EventBus + wire envelope
    └── tts/          ← text-to-speech adapters
"""

from __future__ import annotations

from substrate.integrations.pipeline.data_ref import (
    DataRef,
    DataRefStore,
    DataRefArtifactStore,
)
from substrate.integrations.pipeline.engine import (
    PipelineDef,
    PipelineEngine,
    PipelineResult,
)
from substrate.integrations.pipeline.store import PipelineStore
from substrate.integrations.tools.discovery import CatalogPackage, CapabilityDiscovery
from substrate.integrations.tools.skills._manager import SkillManager
from substrate.integrations.tools.skills._loader import SkillLoader
from substrate.integrations.tools.skills._models import SkillPackage, SkillMetadata

__all__ = [
    "CatalogPackage",
    "CapabilityDiscovery",
    "DataRef",
    "DataRefStore",
    "DataRefArtifactStore",
    "PipelineDef",
    "PipelineEngine",
    "PipelineResult",
    "PipelineStore",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
    "SkillPackage",
]
