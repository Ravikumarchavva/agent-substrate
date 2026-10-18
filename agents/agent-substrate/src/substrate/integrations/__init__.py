"""substrate.integrations — everything that reaches outside this process (L2).

Adapters over the engine's contracts (vendor LLM clients, PostgreSQL/Redis/S3 backends) plus capabilities with
no engine equivalent (tools that call real APIs, RAG, MCP, the sandboxed code interpreter). Importing this package
imports nothing: each adapter is a module of its own and needs only its own extra, so ``import
substrate.integrations.llm.anthropic`` asks for ``anthropic`` and nothing else.

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
    ├── memory/       ← Redis session cache, the memory manager and exposure policy
    ├── database/     ← PostgresDatabase / postgres_store: the engine's state on PostgreSQL (pgvector for vectors)
    ├── storage/      ← S3FileStore
    ├── safety/       ← TextSafetyClassifier, ImageSafetyClassifier
    ├── artifacts/    ← OKF artifact store
    ├── gdpr/         ← cross-store tenant erasure
    ├── triggers/     ← trigger monitors (scheduled, webhooks, events)
    ├── events/       ← Redis-backed EventBus + wire envelope
    └── tts/          ← text-to-speech adapters
"""
