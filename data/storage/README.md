# loamz-data-storage

Loamz Data's embeddable engine, pure Python (pydantic only): SQL written once for SQLite and PostgreSQL, files,
vectors, full-text search, memory and documents. Python 3.13 and 3.14.

```python
from loamz.data.storage import Storage
from loamz.data.storage.documents import Library, Reader

storage = Storage.at("./.loamz")                  # or Storage(postgres(dsn, schema="acme"), "/var/lib/loamz")
await storage.files.upload("tenants/acme/a.txt", b"hello")
await storage.memory.save(record)
library = Library(storage.database, storage.files)
await library.add(pdf_bytes, "q3.pdf", collection="tenants/acme/knowledge/handbook")
```

Extras: `postgres` (asyncpg; vectors need pgvector), `pdf` (pypdfium2), `ocr` (RapidOCR). `loamz.data.storage.models`
holds the embedder and reranker ports, clients for ones reached by URL, and a pool of local `llama-server` children.
`loamz.data.storage.testing` holds the conformance suites every adapter runs (file, memory, vector stores, document
library, extractor, OCR, embedder, reranker).

```bash
uv run --package loamz-data-storage pytest data/storage/tests -n 8   # PostgreSQL via LOAMZ_TEST_POSTGRES_URL
```
