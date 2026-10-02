# Documents — reading, filing, navigating, searching

`substrate.documents` is the one place a user's document is read and what the model does with it. Rationale and what it replaced:
[`decisions.md`](../decisions.md) ("Documents first…"). This page is how it works and where to change it.

## The pieces

| Piece | What it is | Where |
|---|---|---|
| `Reader` | `read(data, filename, *, content_type, strategy) -> ExtractionResult`. A location picks the engine: none = built-in, a URL = a document server, `engine=` = any `DocumentExtractor`. | `documents/reader.py`, `remote.py` |
| Built-in engine | PDFium text layer + headings + figures; DOCX/PPTX/XLSX/ODF/HTML/Markdown/CSV/JSON with the stdlib; OCR only for pages with no text. | `documents/reading/` |
| `Library` | Files documents as an OKF bundle + a derived catalog; `add`, `list`, `outline`, `read`, `find`, `view`, `delete`, `erase_under`, `reindex`. | `documents/library.py` |
| `DocumentsTool` | The one model-facing tool over a `Library`. Collection from `scope_of(ctx)`. | `documents/tool.py` |
| Splitting / chunking | Sections for navigation (`split.py`); chunks for embedding (`chunking.py`). | `documents/` |
| OKF | The bundle format (v0.2) and the stdlib frontmatter codec. | `documents/okf.py`, `_frontmatter.py` |

## Reading

* **Strategies:** `fast` never OCRs · `auto` OCRs only pages with no text · `ocr_only` OCRs every page · `hi_res` needs a layout model, so
  only a document server has one (in-process it degrades to `auto` and sets `degraded_from`).
* **Isolation:** by default the built-in engine runs in a pool of worker processes (`reading/worker.py`, `pool.py`): length-prefixed JSON/bytes
  frames (never pickle), `RLIMIT_AS/FSIZE/CORE`, a wall clock the parent enforces by killing the process group, recycled after 100 documents.
  `Reader(isolate=False)` runs the same code in a thread behind a lock; a custom `Ocr` instance needs it. `ReadLimits` sets size, pages, OCR
  pages, time and memory.
* **Never raises** for a document it cannot read: `success=False` with a reason (a zip/entity bomb, encryption, a legacy `.doc`, garbage).
  A page that is only a picture is OCR'd or listed in `needs_ocr` — never silently empty.
* **By URL:** `POST {url}/v1/extract` with `content_base64`, `filename`, `content_type`, `strategy`; an answer is final (even a refusal), and
  only an unreachable server (connection error, 5xx, timeout) falls back to the built-in engine (`degraded_from` = the URL). The server is
  `apps/document-intelligence`: the library's `Reader` as the baseline, Paddle for `hi_res` or scanned pages, LibreOffice for legacy formats.
* **OCR:** `Tesseract` (the program, via its CLI) by default if installed; RapidOCR with the `ocr` extra is preferred automatically.

## Filing and navigating

```
{collection}/index.md                    what is here
{collection}/{doc}/index.md              type: Index — outline, engine, sha256, needs_ocr
{collection}/{doc}/NN-{slug}.md          type: Section — Prev/Up/Next links
{collection}/{doc}/images/{id}.png       figures, charts, table crops
```

* The **bundle is the source of truth**; the catalog (`library_documents`, `library_sections` with a full-text index, `library_images`) is
  derived and rebuilt by `reindex(collection=…)`. Adding the same bytes again is a no-op (the id is the name's slug + 6 hex of the SHA-256).
* Sections are cut at the shallowest heading level with ≥2 headings, small ones merged, large ones split by heading, page, then paragraph;
  with no headings, by whole pages. Every section keeps its `<!-- page N -->` markers, so a citation names a page.
* `DocumentsTool` actions: `list` (cursor), `outline` (a document's sections, or one section's headings), `read` (≤24,000 characters, with a
  continuation `offset`), `find`, `view` (one figure). There is **no action that adds a document or opens a path**; the collection comes from
  the run's scope, never an argument. Text comes back inside `<document>` tags — untrusted data. Results carry `[n]` citations (stable per
  tool instance) in the wire shape the chat UI renders.

## Searching by meaning (knowledge bases)

`Library(store, embedder=…, reranker=…)` — an `EmbeddingModel`/`Reranker`, or a URL (`RemoteEmbedder`/`RemoteReranker`, `models/remote.py`:
the OpenAI embeddings wire and the Jina/Cohere rerank wire; `apps/embedding-reranker` serves Qwen3-VL on them, text and images in one space).

* `add` also chunks each section to the embedder's `max_input_tokens`, embeds (figures with their captions, if the model takes images) and
  stores in `store.vectors`. A collection holds one embedder's vectors (`VectorSpaceError` otherwise, via `vector_spaces`).
* `find` fuses similarity with any-word full text, reranks the top 20 if there is a reranker, and folds chunks into sections. If a service is
  down it answers with words and a note; chunks stored without a vector are embedded later by `reindex(missing_only=True)`. An `add` never
  fails because a service is down.
* With no embedder, `find` is words only — which is all a conversation's documents need.
* The folder store scans exactly (about 1 s per 10,000 chunks at 2,048 dimensions; a warning is logged above that); large knowledge bases
  belong on `postgres_store` (pgvector HNSW).

## In the platform (`apps/substrate-cloud`)

* One `Reader` (`document_reader.py`): the document-intelligence URL if `DOCUMENT_INTELLIGENCE_SERVICE_URL` is set, else built-in.
* A conversation's uploads are read once, filed under `conversation_documents_prefix(tenant, user, conversation)`, and the
  `.extracted.md` sidecar is written from the same read. A short document is inlined into the prompt; a long one as its outline.
* A knowledge base is `knowledge_collection(tenant, kb)`. `/rag/*` and `/internal/knowledge/*` derive it from the authenticated claims, never
  from the request — a tenant cannot reach another's. The chat has two tools over the two libraries: `documents` and `knowledge`
  (`KNOWLEDGE_CHAT_BASE`). Embedder/reranker: `EMBEDDING_RERANKER_SERVICE_URL`, or `EMBEDDING_MODEL`; with neither, words only.
* GDPR erasure calls `Library.erase_under(prefix)` for the user or tenant.

## Where to change things

| To… | Do |
|---|---|
| Read another format | a reader under `documents/reading/`, a case in `sniff.py` and `engine.py`; add a fixture and a row in `tests/documents/test_formats.py` |
| Use another OCR / reader / document server | implement `Ocr` / `DocumentExtractor` and pass it; run `OcrConformance` / `DocumentExtractorConformance` |
| Use another embedder / reranker | implement `EmbeddingModel` / `Reranker` and pass it (a URL needs no code); run `EmbeddingModelConformance` / `RerankerConformance` |
| Change how sections are cut | `documents/split.py` (and `tests/documents/test_split.py`) |
| Change what the model can do with documents | `documents/tool.py`; keep it free of any argument that names a collection or a path |

Tests: `tests/documents/` (formats, PDF, OCR, reader, library on SQLite **and** PostgreSQL, embeddings), invariants I32–I34 and the tenant
row in `tests/invariants/test_documents.py`, `apps/substrate-cloud/tests/test_rag_routes.py` for tenant isolation.
