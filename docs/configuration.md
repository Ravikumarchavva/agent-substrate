# Configuration Reference

This guide documents the configuration settings for Agent Substrate (`SubstrateConfig`), their defaults, and the architectural context behind them.

Settings are managed via Pydantic (`pydantic_settings.BaseSettings`). In the library layer, `SubstrateConfig` reads directly from system environment variables. When running the built-in FastAPI server, `ServerSettings` extends this class to add server-level fields (JWT, CORS, rate limits, observability) and `.env` file loading.

---

## 1. LLM Provider Keys & Endpoints

| Environment Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | `""` | OpenAI API key |
| `ANTHROPIC_API_KEY` | `""` | Anthropic API key |
| `GEMINI_API_KEY` | `""` | Google Gemini API key |
| `GROQ_API_KEY` | `""` | Groq API key |
| `NVIDIA_API_KEY` | `""` | NVIDIA API key |
| `OPENROUTER_API_KEY` | `""` | OpenRouter API key |
| `EXA_API_KEY` | `""` | Exa web search API key |
| `TAVILY_API_KEY` | `""` | Tavily web search API key |
| `OPENAI_BASE_URL` | `""` | Custom OpenAI base URL (e.g. vLLM, Ollama, LocalAI) |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Groq API base URL |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter base URL |
| `OPENROUTER_SITE_URL` | `http://localhost:3000` | OpenRouter app HTTP Referer header |
| `OPENROUTER_APP_NAME` | `Agent Substrate` | OpenRouter app title header |
| `HF_TOKEN` | `""` | HuggingFace user token for downloading gated models and tokenizers. Passed through directly to subprocess environments. |

---

## 2. Database & Row-Level Security (RLS)

Agent Substrate supports multi-tenant PostgreSQL with Row-Level Security (RLS) to ensure strict tenant and user isolation.

| Environment Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `""` | Synchronous / administrative database connection string |
| `ASYNC_DATABASE_URL` | `""` | Primary async connection string for SQLAlchemy / asyncpg |
| `APP_DATABASE_URL` | `""` | Restricted application connection string used once RLS is provisioned |
| `RLS_APP_ROLE_PASSWORD` | `None` | Password for the dedicated `substrate_app` role |

### RLS Architecture
- **Admin / Bootstrap Connection (`DATABASE_URL`, `ASYNC_DATABASE_URL`)**:
  Requires table-owner or superuser privileges. Used during startup for schema creation, additive migrations, and creating RLS policies/roles (`rls.py`).
- **Restricted App Role (`APP_DATABASE_URL`)**:
  Connects as `substrate_app`. Possesses standard DML rights (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) but lacks superuser status, ensuring PostgreSQL enforces RLS tenant and user filters on every query.
- If `APP_DATABASE_URL` is unset, queries fall back to the primary database URL (where RLS policies remain enabled, but are bypassed if connecting as superuser).

---

## 3. Connection Pooling & Runtime Backend

| Environment Variable | Default | Description |
|---|---|---|
| `RUNTIME_BACKEND` | `postgres` | Runtime persistence engine (`postgres` for durable event logs/mailboxes, `memory` for ephemeral in-process dev/testing) |
| `RUNTIME_PG_POOL_MIN_SIZE` | `2` | Minimum asyncpg connections allocated to the durable runtime |
| `RUNTIME_PG_POOL_MAX_SIZE` | `10` | Maximum asyncpg connections allocated to the durable runtime |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `REDIS_SESSION_TTL` | `3600` | Redis session expiration TTL in seconds |

> [!NOTE]
> The durable runtime (`EventLog`, `Inbox`, `Scheduler`, `Supervisor`) maintains a **separate** asyncpg connection pool from the application ORM pool. Calculate database connection capacity as:
> `Total Connections = (Runtime Pool + ORM Engine Pool) × Replicas`.

---

## 4. Code Interpreter Sandbox Isolation

Agent Substrate provides secure, isolated execution environments for executing untrusted LLM-generated code.

| Environment Variable | Default | Description |
|---|---|---|
| `SANDBOX_RUNTIME` | `nsjail` | Sandbox backend: `nsjail`, `k8s`, or `inprocess` |
| `SANDBOX_NETWORK_POLICY` | `deny` | Network egress policy: `deny`, `pip_only`, or `full` |
| `SANDBOX_TIMEOUT_SECONDS` | `60` | Execution timeout per code block |
| `SANDBOX_MEMORY_BYTES` | `2147483648` (2 GB) | Memory ceiling enforced by cgroups |
| `SANDBOX_SESSION_TTL_SECONDS`| `3600` | Idle session reaping TTL for sandbox pods |
| `SANDBOX_RUNTIME_CLASS` | `""` | Kubernetes `RuntimeClass` (e.g. `gvisor` for container sandboxing) |
| `SANDBOX_PYTHON` | `""` | Path to a dedicated Python interpreter/virtual environment mounted read-only inside the sandbox |
| `CI_WORKSPACE_PVC_CLAIM` | `""` | Persistent volume claim name for Kubernetes sandbox workspace sharing |

### Sandbox Runtimes
- **`nsjail` (Default)**:
  Uses Linux namespaces (`CLONE_NEWPID`, `CLONE_NEWNS`, `CLONE_NEWNET`, `CLONE_NEWIPC`, `CLONE_NEWUTS`) and cgroup v2 subtree limits on the host. No daemon or nested virtualization required.
  - **Mount Isolation**: Minimal host paths (`/lib`, `/usr`, `/etc/fonts`) are mounted read-only. Only the caller's specific session directory is mounted read-write at `/workspace`.
  - **Memory & Process Caps**: Caps memory via `memory.max` and `memory.swap.max` (stopping kernel swap fallback), and sets max processes (`pids.max = 64`) to prevent fork-bombs.
  - **Network Enforcement**: Enforces zero network access (`CLONE_NEWNET`) unless explicitly overridden.
- **`k8s`**:
  Deploys an agent-sandbox pod per user session using Kubernetes PVC subPaths, with optional gVisor kernel sandboxing.
- **`inprocess`**:
  Runs directly in the host Python process with **zero isolation**. Intended strictly for local testing and CI.

---

## 5. Knowledge Base & RAG Pipeline

| Environment Variable | Default | Description |
|---|---|---|
| `RAG_BACKEND` | `local` | RAG implementation: `local` (self-hosted pgvector + RAG pipeline) or `pinecone` (managed Pinecone Assistant) |
| `PINECONE_API_KEY` | `""` | Pinecone API key (when using Pinecone backend) |
| `PINECONE_ASSISTANT_NAME` | `""` | Name of the Pinecone Assistant |
| `RAG_TEXT_EMBEDDING_DIM` | `1536` | Dimension of text embeddings (must match model output, e.g. 1536 for OpenAI `text-embedding-3-small`) |
| `RAG_IMAGE_EMBEDDING_DIM` | `2048` | Dimension of multimodal image embeddings (must match model output, e.g. 2048 for `Qwen3-VL-Embedding-2B`) |
| `RAG_MAX_DOC_PAGES` | `20` | Maximum page count allowed for synchronous document upload |
| `RAG_MAX_DOC_MB` | `5` | Maximum file size in MB for uploaded documents |
| `RAG_DAILY_DOC_LIMIT` | `20` | Daily document commitment quota per user |
| `RAG_DAILY_UPLOAD_ATTEMPT_LIMIT` | `100` | Daily upload attempt ceiling to prevent extraction abuse |
| `RAG_CHUNK_SIZE` | `512` | Token chunk size for document splitting |
| `RAG_CHUNK_OVERLAP` | `128` | Token overlap between adjacent chunks |

### Hybrid Search & Reranking Budgets
The local RAG pipeline narrows candidates across multi-stage retrieval budgets:
1. **Dense Retrieval (`RAG_DENSE_K = 50`)**: Top vector distance hits.
2. **Lexical Retrieval (`RAG_LEXICAL_K = 50`)**: Top full-text search hits.
3. **Reciprocal Rank Fusion (`RAG_FUSED_K = 50`)**: Merged candidate list.
4. **Pre-filter (`RAG_RERANK_TOP_N = 10`)**: Narrowed candidate set passed to the reranker.
5. **Multimodal Reranking (`RAG_FINAL_K = 5`, `RAG_MIN_RERANK_SCORE = 0.1`)**: Final ranked results; scores below threshold are dropped.

---

## 6. Document Intelligence & Multimodal Sidecars

| Environment Variable | Default | Description |
|---|---|---|
| `DOCUMENT_INTELLIGENCE_SERVICE_URL` | `""` | URL to isolated PaddleOCR layout/table detection service |
| `DOCUMENT_INTELLIGENCE_AUTH_TOKEN` | `""` | Bearer token for document intelligence service |
| `DOCUMENT_INTELLIGENCE_TIMEOUT_S` | `90` | Request timeout in seconds |
| `EMBEDDING_RERANKER_SERVICE_URL` | `""` | URL to multimodal embedding & reranking proxy service |
| `EMBEDDING_RERANKER_AUTH_TOKEN` | `""` | Bearer token for embedding & reranking service |
| `EMBEDDING_RERANKER_TIMEOUT_S` | `30` | Request timeout in seconds |

---

## 7. Storage & Workspaces

| Environment Variable | Default | Description |
|---|---|---|
| `FILE_STORE_BACKEND` | `local` | Storage provider: `local` (filesystem/PVC), `s3` (S3/SeaweedFS), `memory` (tests) |
| `FILE_STORE_ROOT` | `./data/workspaces` | Directory root for local file storage |
| `FILE_STORE_BUCKET` | `agent-files` | S3 bucket name |
| `FILE_STORE_ENDPOINT` | `None` | S3 endpoint URL (for MinIO / SeaweedFS) |
| `FILE_STORE_REGION` | `us-east-1` | S3 region |
| `FILE_STORE_ACCESS_KEY` | `None` | S3 access key |
| `FILE_STORE_SECRET_KEY` | `None` | S3 secret key |
| `FILE_MAX_UPLOAD_BYTES` | `209715200` (200 MB) | Maximum upload size per file |
| `WORKSPACE_USER_QUOTA_BYTES`| `1073741824` (1 GB) | Maximum cumulative disk quota per user |
| `WORKSPACE_USER_DELETE_ALLOWED` | `True` | Whether users are permitted to delete workspace files |

