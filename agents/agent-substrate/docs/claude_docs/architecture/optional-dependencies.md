# Optional dependencies — what each extra is for, and why it's opt-in

`pyproject.toml`'s `[project.optional-dependencies]` keeps only a one-line
comment per extra so the install story stays scannable for anyone deciding
what to `pip install`/`uv add`. This doc holds the full rationale — size
costs, version-pin reasoning, mutual-exclusivity rules — for when the
one-liner isn't enough.

## The base install

`pip install agent-substrate` is the engine: `pydantic`, `opentelemetry-api`, and `pypdfium2` — so a plain install **reads documents**
(PDF through PDFium; DOCX/PPTX/XLSX/ODF/HTML/Markdown/CSV with the standard library). Row I31 (`tests/invariants/test_library.py`)
fails the build if a driver, SDK or framework creeps into `dependencies`, or if importing any core concept package loads one.
Everything else is an extra named for what it enables, and an adapter package imports nothing until a name is used (I32),
so asking for one vendor's client needs that vendor's SDK and nothing else.

| Extra | Enables |
|---|---|
| `openai` | OpenAI and OpenAI-compatible chat/embedding clients (vLLM, llama.cpp, Ollama, LM Studio) — `openai`, `tiktoken`, `httpx`, `pillow` |
| `anthropic`, `gemini` | those vendors' clients |
| `postgres` | `integrations.database.postgres_store` — the whole store on PostgreSQL (`asyncpg`); vectors also need pgvector |
| `redis` | session cache, event bus |
| `scheduler` | scheduled/cron triggers (`apscheduler`) |
| `mcp` | MCP tools |
| `tts` | text-to-speech |
| `tools` | skills and curated artifacts (`pyyaml`) |
| `console` | the interactive console (`rich`, `prompt-toolkit`, `pydantic-settings`) |
| `serve` | `substrate.server.create_app` (`fastapi`) |
| `testing` | the conformance suites in `substrate.testing` (`pytest`, `pytest-asyncio`, `httpx`) |
| *(no `server` extra)* | the multi-tenant platform is `apps/substrate-cloud`, a project of its own that depends on the extras above plus SQLAlchemy, uvicorn, JWT and the OpenTelemetry SDK |

Logging follows the same rule (I33–I34): the library emits through `logging.getLogger(__name__)` and installs only a
`NullHandler`; `substrate.logger.setup_logging` is for the application's entry point.

## `web`

WebSearchTool's DuckDuckGo fallback, ReadUrlTool's crawl4ai fallback,
WebSurferTool. Playwright alone is ~140MB; crawl4ai pulls a further ~250MB
of transitive deps (scipy/pandas/onnxruntime via its own extras) for what
is only ever the free-tier fallback behind Tavily/Exa.

## `code`

K8s agent-sandbox code execution (`SANDBOX_RUNTIME=k8s`). Pulls the full
`kubernetes`/`kubernetes_asyncio` client libraries (~85MB) — only needed if
you're actually running sandbox pods against a real cluster. The default
`nsjail` runtime needs none of it.

## `sandbox`

Data-science packages the `code_interpreter` tool advertises to the model.
With `SANDBOX_RUNTIME=nsjail` the sandbox executes using an interpreter
on the host running agent-substrate (see `SANDBOX_PYTHON`), so these must be
importable from *that* interpreter's environment — previously they only
existed inside the sandbox container image. Install into the engine's own
env (simplest), or into a dedicated venv and point `SANDBOX_PYTHON` at it to
keep them out of the engine.

## `ocr`

RapidOCR for scanned pages: PaddleOCR's own small models on ONNX Runtime, **inside the wheel** (nothing is downloaded at read time) —
`rapidocr` + `onnxruntime`, about 120–150 MB because `rapidocr` pins the full GUI build of OpenCV (`opencv-python`; on a headless server
install `opencv-python-headless` first). It is preferred automatically over Tesseract when installed.

Without it the base install OCRs with the **`tesseract` program** if it is on the `PATH` (`apt install tesseract-ocr`), driven through its
CLI, so it costs no Python package at all; with neither, a page that is only a picture is reported in `needs_ocr`, never returned silently
empty. Both engines run in the reader's isolated worker process. For layout, tables and charts use the document server below.

## `s3`

S3-compatible object storage backend for `FILE_STORE_BACKEND=s3` (the
default, `"local"`, needs neither of these).

## `safety`

`MultimodalSafetyMiddleware`'s jailbreak-text (`PromptGuardClassifier`) and
NSFW-image (`ImageSafetyClassifier`) classifiers — `onnxruntime` +
`tokenizers` + `huggingface-hub` to load and run the ONNX models, plus
`confusable-homoglyphs` for the text normalizer both share. All four are
lazy-imported only inside those two classes' `__init__`, never at module
top level, so nothing else in the codebase needs them.

Deliberately opt-in despite the reference monolith enabling this guardrail
by default (`ENABLE_TEXT_SAFETY_GUARD=true`): `build_safety_middleware()`
(`infrastructure/serving_factory.py`) already wraps classifier construction
in a fail-open `try/except` — a missing package is handled exactly like a
model-download failure on first run, logged loudly, guardrail disabled,
monolith still boots. Installing `[safety]` (or running the platform app, which includes
it) is what keeps the guardrail actually active.

## `server`

Everything the reference monolith server (`substrate up && uv run start`)
needs beyond the base install — the base `dependencies` already cover
Postgres, Redis, FastAPI, and Pillow (this is a *deployable app* package,
not a headless library), so `server` only adds the optional features layered
on top: web search/browsing, the K8s sandbox runtime, S3 storage,
the safety guardrail, and the code-interpreter's data-science packages.
Shorthand for `agent-substrate[web,code,s3,safety,sandbox]`.

## `logging`

The JSON formatter for `substrate.logger.setup_logging` (`python-json-logger`, `msgspec`): what an *application* (a service)
needs to configure logging. A library never calls it.

## Heavy services (`apps/`, `packages/`)

Layout analysis and multimodal embedding — anything that needs gigabytes of dependencies or a GPU — runs as a server, and the library
only holds the client, in the core and standard-library only: `Reader("http://…")` (`documents/remote.py`) and
`RemoteEmbedder` / `RemoteReranker` (`models/remote.py`). A URL in, a typed result out; a server that is down is a typed
`ServiceUnavailableError` (or, for a `Reader`, a fall-back to the built-in engine). Each server is a project
of its own with its own `pyproject.toml`, environment, tests and Dockerfile, so installing the library never installs them and
upgrading one never touches another:

* `apps/document-intelligence` — the library's own `Reader` as the baseline (every format, isolated), PaddleOCR layout/chart/table
  extraction for PDFs and images when asked for (`hi_res`) or when the baseline found scanned pages, LibreOffice for legacy
  `.doc/.ppt/.xls/.rtf` only, and a pre-parse security scan. Extras `paddle` (CPU wheel) or `paddle-gpu`, pinned to the wheel
  versions verified on the target hardware and served from PaddlePaddle's own indexes (declared in that project, not here).
* `apps/embedding-reranker` — Qwen3-VL embedding and reranking, a thin proxy in front of llama-server, on the OpenAI embeddings
  wire (`/v1/embeddings`, `/v1/models`) and the Jina/Cohere rerank wire (`/v1/rerank`). No model library at all. This is the
  default knowledge-base backend; llama.cpp, vLLM, Ollama and TEI serve the same endpoints, so `RemoteEmbedder` works with them too.
* `packages/inference-pool` — spawning, supervising and load-balancing llama-server children (or pointing at remote ones), and
  GPU/CPU/RAM detection; shared by both services.

`make test-apps` installs each into a fresh environment and runs its tests there.

## `sentence-transformers`

`create_embedding_client("sentence-transformers/<model>")`
(`integrations/llm/factory.py`) — local, CPU-or-CUDA, no API key, no
external server. This is the one place `torch` comes back after being
deliberately removed everywhere else (see "Heavy services" above) —
genuinely needed here, not an oversight, since `sentence-transformers` has
no lighter runtime. Opt-in only: without this extra, selecting this
provider raises a clean `ModuleNotFoundError` instead of silently working.
`torch` itself is routed to PyTorch's own CPU-only wheel index
(`[[tool.uv.index]] name = "pytorch-cpu"`) rather than the default PyPI
wheel, which bundles the full NVIDIA CUDA/cuDNN/NCCL runtime even for
CPU-only use (confirmed: `nvidia-cufile`, `nvidia-curand`, etc. all pulled
in by a plain `torch` install otherwise).
