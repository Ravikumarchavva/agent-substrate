# Optional dependencies — what each extra is for, and why it's opt-in

`pyproject.toml`'s `[project.optional-dependencies]` keeps only a one-line
comment per extra so the install story stays scannable for anyone deciding
what to `pip install`/`uv add`. This doc holds the full rationale — size
costs, version-pin reasoning, mutual-exclusivity rules — for when the
one-liner isn't enough.

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

## `rag`

`PDFLoader` (the local, no-extraction-service fallback path) plus
`LanceDBVectorStore` (embedded, file-based vector store —
`capabilities/vector/lancedb_store.py`). ~110MB combined (`lancedb` +
`pyarrow`) — not needed unless you actually use either.

## `chunking`

Model-based sentence segmentation for the chunkers (`SaTSegmenter`, in
`capabilities/knowledge/segmentation.py`). `wtpsplit` pulls `transformers` +
`huggingface-hub` + `scikit-learn` + `pandas`, and needs a backend to
actually run a model — `torch` if present, otherwise `wtpsplit[onnx-cpu]`
(what's pinned here). The default `RegexSegmenter` needs none of it, so
this stays opt-in: install it only if punctuation is an unreliable boundary
signal for your documents (OCR'd PDFs, headings, list items), which is
where SaT earns its weight.

The `onnx-cpu` sub-extra is not optional in practice: `wtpsplit` declares NO
deep-learning backend of its own and fails at model construction with
*"Please install `torch` to use WtP with a PyTorch model"* unless `torch` or
`onnxruntime` is present. `onnxruntime` is ~50MB against `torch`'s ~2GB, so
`SaTSegmenter` defaults to the ONNX path — see its docstring to opt back
into `torch`.

## `rag-pinecone`

Managed RAG via Pinecone Assistant (`RAG_BACKEND=pinecone`) — parsing,
chunking, embedding, storage, and retrieval all run on Pinecone's side, so
none of the local `rag`/`document-intelligence` extras are needed with this
backend.

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
monolith still boots. Installing `[safety]` (or `[server]`, which includes
it) is what keeps the guardrail actually active.

## `server`

Everything the reference monolith server (`substrate up && uv run start`)
needs beyond the base install — the base `dependencies` already cover
Postgres, Redis, FastAPI, and Pillow (this is a *deployable app* package,
not a headless library), so `server` only adds the optional features layered
on top: web search/browsing, the K8s sandbox runtime, local RAG, S3 storage,
the safety guardrail, and the code-interpreter's data-science packages.
Shorthand for `agent-substrate[web,code,rag,s3,safety,sandbox]`.

## `document-intelligence` / `document-intelligence-gpu`

Layout-aware document parsing (PDF layout, chart/table detection, OCR) via
PaddleOCR — used only by `runtimes/document_intelligence/service/`, never
imported from the main API process. Multimodal embedding and reranking are
a separate service (`runtimes/embedding_reranker/`) — thin HTTP calls to
the `llama-embed`/`llama-rerank` `llama-server` sidecars — so
`sentence-transformers`/`torch` aren't needed here either. Not needed
unless you actually run the document-intelligence service; the base
install already covers CSV/JSON/text/`pypdf`-based PDF loading without it.

`paddlepaddle` (CPU) is pinned to the exact wheel version verified working
on this project's own target hardware, served from PaddlePaddle's own CPU
package index (`[[tool.uv.index]] name = "paddle-cpu"`), not plain PyPI.
`paddlepaddle-gpu` is the CUDA 13.0 wheel from the matching GPU index
(`paddle-cu130`) — mutually exclusive with the CPU extra (pick one, not
both; both provide the `paddle` import and will conflict). Pick the CUDA
index (cu118/cu126/cu130/...) matching your driver — `cu130` is what this
project's own dev GPU (CUDA 13.1 driver) uses. A CPU-only wheel without the
exact pin also exists on plain PyPI and is what a downstream resolver falls
back to.

**Note for downstream consumers:** `[tool.uv.sources]`/`[[tool.uv.index]]`
in this repo's `pyproject.toml` only apply when *this* repo is the active
uv project — they don't propagate to a project that merely depends on
`agent-substrate`. Replicate the same `[tool.uv.sources]` entry in your own
project if you need that exact pinned/verified wheel rather than whatever
PyPI resolves to.

## `sentence-transformers`

`create_embedding_client("sentence-transformers/<model>")`
(`integrations/llm/factory.py`) — local, CPU-or-CUDA, no API key, no
external server. This is the one place `torch` comes back after being
deliberately removed everywhere else (see `document-intelligence` above) —
genuinely needed here, not an oversight, since `sentence-transformers` has
no lighter runtime. Opt-in only: without this extra, selecting this
provider raises a clean `ModuleNotFoundError` instead of silently working.
`torch` itself is routed to PyTorch's own CPU-only wheel index
(`[[tool.uv.index]] name = "pytorch-cpu"`) rather than the default PyPI
wheel, which bundles the full NVIDIA CUDA/cuDNN/NCCL runtime even for
CPU-only use (confirmed: `nvidia-cufile`, `nvidia-curand`, etc. all pulled
in by a plain `torch` install otherwise).
