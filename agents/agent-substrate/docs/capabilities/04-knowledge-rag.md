# 4 · Knowledge & RAG

The knowledge sub-package wires together embedding clients, vector stores, chunkers, and an optional knowledge graph into two high-level pipelines: `RAGPipeline` (vector similarity only) and `GraphRAGPipeline` (vector + graph traversal).

## Two pipelines

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef pipe  fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef comp  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef store fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef llm   fill:#E3F2FD,stroke:#1565C0,color:#0D47A1
    classDef proto fill:#FFF3E0,stroke:#E65100,color:#BF360C

    LOADERS["Document Loaders (knowledge/loaders/)<br/>TextLoader · PDFLoader · CSVLoader<br/>JSONLoader · DoclingLoader → list[Document]"]:::comp

    subgraph RAG["RAGPipeline — pipeline.py"]
        direction TB
        CHUNK["Chunker · chunking.py<br/>TextChunker(chunk_size, overlap) · SentenceChunker<br/>chunk(text, metadata) → list[Document]"]:::comp
        EMB["EmbeddingClient (kernel Protocol)<br/>embed(texts) → list[list[float]]<br/>embed_single(text) → list[float]"]:::llm
        VS["VectorStore (kernel Protocol)<br/>add(docs, collection) · delete(ids)<br/>search(vec, limit, filter) → list[SearchResult]"]:::store
        RNK["Reranker · reranker.py (optional)<br/>cross-encoder rerank(query, results, top_k)"]:::comp
        CHUNK ~~~ EMB ~~~ VS ~~~ RNK
    end

    subgraph GRAG["GraphRAGPipeline — graph_rag.py"]
        direction TB
        RAG2["embeds a RAGPipeline (same vector path)"]:::pipe
        GLLM["LLMClient — entity + relationship extraction<br/>prompt → {entities:[...], relationships:[...]}"]:::llm
        GS["GraphStore (kernel Protocol)<br/>add_entities · add_relationships<br/>get_subgraph(entities, depth) · query_cypher"]:::store
        RAG2 ~~~ GLLM ~~~ GS
    end

    PROTO["RAGProvider Protocol · knowledge/protocol.py<br/>ingest · query · query_with_context<br/>satisfied by both pipelines"]:::proto

    LOADERS -->|"list[Document]"| RAG
    RAG -->|"embedded by"| GRAG
    RAG -.->|"implements"| PROTO
    GRAG -.->|"implements"| PROTO
```

## Ingest flow

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TD
    classDef proc  fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef store fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef io    fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef gcls fill:#fce4ec,stroke:#880E4F,color:#880E4F

    IN(["content: str | list[str]<br/>collection: str = 'default'<br/>chunker: 'text' | 'sentence'"]):::io

    CHUNK["Chunker.chunk(text, metadata)<br/>──────────────────<br/>Document(id=uuid4, text=chunk,<br/>  metadata: dict, embedding: None)<br/>for TextChunker: fixed-size split by chars<br/>for SentenceChunker: sentence boundary split<br/>→ list[Document]"]:::proc

    EMBED["EmbeddingClient.embed(chunk_texts)<br/>──────────────────<br/>sends all chunk texts in one API call<br/>(batch to minimise round-trips)<br/>→ list[list[float]]  (one vector per chunk)"]:::proc

    MERGE["zip(documents, embeddings)<br/>──────────────────<br/>dataclasses.replace(doc, embedding=emb)<br/>→ list[Document]  (embedding filled in)"]:::proc

    STORE["VectorStore.add(docs, collection)<br/>──────────────────<br/>PgVectorStore: INSERT INTO vector_store_{collection}<br/>  (id, text, content_json, metadata, embedding)<br/>creates HNSW index on first add<br/>→ list[str]  (inserted IDs)"]:::store

    OUT(["returns: int  (chunk count inserted)"]):::io

    subgraph GRAPHONLY["GraphRAGPipeline only (extract_graph=True)"]
        style GRAPHONLY fill:#fce4ec,stroke:#880E4F,color:#880E4F
        LLM_EXT["LLMClient.generate(prompt)<br/>extract entities + relationships<br/>from chunk text as JSON"]:::gcls
        GS_ADD["GraphStore.add_entities(list[Entity])<br/>GraphStore.add_relationships(list[Relationship])<br/>AGEGraphStore: openCypher via Apache AGE"]:::gcls
        LLM_EXT --> GS_ADD
    end

    IN --> CHUNK --> EMBED --> MERGE --> STORE --> OUT
    STORE -.->|"for each chunk"| LLM_EXT
```

## Query flow

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TD
    classDef proc  fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef store fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef io    fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef gcls fill:#fce4ec,stroke:#880E4F,color:#880E4F
    classDef dec   fill:#FFF3E0,stroke:#E65100,color:#BF360C,font-weight:bold

    Q(["question: str<br/>collection: str<br/>limit: int = 5"]):::io

    EMBS["EmbeddingClient.embed_single(question)<br/>──────────────────<br/>→ list[float]  (query vector)"]:::proc

    SEARCH["VectorStore.search(query_vec, collection, limit, filter)<br/>──────────────────<br/>PgVectorStore: SELECT ... ORDER BY<br/>  embedding <=> $query_vec LIMIT $limit<br/>→ list[SearchResult(id, text, score, metadata)]"]:::store

    RERANK{"Reranker<br/>configured?"}:::dec

    RNK["Reranker.rerank(question, results, top_k)<br/>──────────────────<br/>cross-encoder model scores each (question, chunk)<br/>pair → re-sorted list[SearchResult]"]:::proc

    GRAPHONLY["GraphRAGPipeline only:<br/>GraphStore.get_subgraph(entities, depth=2)<br/>──────────────────<br/>extracts entity names from search results<br/>openCypher: MATCH (n)-[*1..{depth}]-(m)<br/>returns SubGraph{entities, relationships}<br/>appended to context window"]:::gcls

    MODE{"query_with_context<br/>called?"}:::dec

    GEN["LLMClient.generate(messages)<br/>──────────────────<br/>system: 'Answer using context below'<br/>user: question + retrieved chunks<br/>→ str  (generated answer)"]:::proc

    OUT1(["list[SearchResult]<br/>— from query()"]):::io
    OUT2(["str  (generated answer)<br/>— from query_with_context()"]):::io

    Q --> EMBS --> SEARCH --> RERANK
    RERANK -->|"yes"| RNK --> MODE
    RERANK -->|"no"| MODE
    SEARCH -.->|"GraphRAG path"| GRAPHONLY --> MODE
    MODE -->|"yes"| GEN --> OUT2
    MODE -->|"no"| OUT1
```

## `RAGPipeline` API

```python
pipeline = RAGPipeline(
    embedding_client=embed_client,   # EmbeddingClient Protocol
    vector_store=vector_store,       # VectorStore Protocol
    default_chunk_size=512,
    default_chunk_overlap=128,
)

# Ingest text
n = await pipeline.ingest("Long document …", collection="kb", chunker="text")

# Ingest pre-chunked documents (e.g. from a loader)
n = await pipeline.ingest_documents(docs, collection="kb")

# Retrieve
results: list[SearchResult] = await pipeline.query("What is X?", collection="kb", limit=5)

# Full RAG: retrieve + generate
answer: str = await pipeline.query_with_context(
    "What is X?",
    collection="kb",
    model_client=llm_client,
)
```

## `GraphRAGPipeline` API

```python
pipeline = GraphRAGPipeline(
    rag_pipeline=rag_pipeline,
    graph_store=graph_store,
    model_client=model_client,
)

# Ingest into vector store + extract entities into graph
n = await pipeline.ingest_with_graph("Document …", collection="kb", extract_graph=True)

# Query — vector results enriched with graph neighbours
results = await pipeline.query("Who works at Acme?", collection="kb")
```

## Chunkers

| Class | Strategy | Parameters |
|---|---|---|
| `TextChunker` | Fixed-size character split with overlap | `chunk_size=512`, `overlap=128` |
| `SentenceChunker` | Sentence-boundary split | `max_chunk_size` |

```python
from ravi.capabilities.knowledge.chunking import get_chunker

chunker = get_chunker("text", chunk_size=512, overlap=128)
docs = chunker.chunk("Long text …", metadata={"source": "readme.md"})
```

## Document loaders

All loaders implement the `DocumentLoader` base protocol: `async def load(path) -> list[Document]`.

| Module | Class | Formats |
|---|---|---|
| `text_loader.py` | `TextLoader` | `.txt`, `.md` |
| `pdf_loader.py` | `PDFLoader` | `.pdf` (pypdf) |
| `csv_loader.py` | `CSVLoader` | `.csv` |
| `json_loader.py` | `JSONLoader` | `.json` |
| `docling_loader.py` | `DoclingLoader` | `.pdf`, `.docx`, `.pptx`, `.html` via docling |

## `RAGProvider` Protocol

Both pipelines satisfy the `RAGProvider` protocol (`knowledge/protocol.py`):

```python
class RAGProvider(Protocol):
    async def ingest(self, content, *, collection="default", **kwargs) -> int: ...
    async def query(self, question, *, collection="default", limit=5, **kwargs) -> list[SearchResult]: ...
    async def query_with_context(self, question, *, collection="default", limit=5, **kwargs) -> str: ...
```

The `KnowledgeSearchTool` (`capabilities/tools/ai/knowledge_search.py`) accepts any `RAGProvider` — swap `RAGPipeline` for `GraphRAGPipeline` without changing the tool.

## Reranker

`capabilities/knowledge/reranker.py` provides a cross-encoder reranker that can post-process `VectorStore.search()` results before they are passed to the LLM. It is optional — use it when recall quality matters more than latency.

```python
from ravi.capabilities.knowledge.reranker import Reranker

reranker = Reranker(model="cross-encoder/ms-marco-MiniLM-L-6-v2")
reranked = await reranker.rerank(query, results, top_k=3)
```

## Paged memory pipeline

`knowledge/page_pipeline.py` handles very long documents by splitting them into pages rather than fixed-size chunks. Use it when the document has natural page boundaries (PDFs) and you want to preserve page-level context.
