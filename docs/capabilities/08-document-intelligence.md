# Document Intelligence & Security Scanning

This document details the document processing runtime: pre-parse structural security scanning (`doc-firewall`) and layout-aware document extraction (`PaddleOCR` / `PP-StructureV3`).

---

## 1. Structural Security Scanning (`doc-firewall`)

Before untrusted document bytes (e.g. PDFs) are handed to heavy parser engines or image extractors, they pass through `scan_document()` in `substrate.runtimes.document_intelligence.security_scan`.

### Detection & Threat Categorization
- **Pre-Parser Defense**: Blocks malicious active content (e.g., `/JavaScript`, `/JS` OpenAction annotations) without requiring heavy ML or PyTorch dependencies.
- **Threat Classification**:
  - `doc-firewall` emits both `ALLOW`, `BLOCK`, and `REVIEW` findings.
  - In reduced-coverage mode, structural byte-density counts (`T6_DOS`, `T3_OBFUSCATION`) can trigger on benign, complex PDFs (e.g., Wikipedia exports with dense object tables).
  - Agent Substrate classifies `T6_DOS` and `T3_OBFUSCATION` as non-fatal warnings (structural-only), while active content threats (`T2_ACTIVE_CONTENT`, embedded exploits, shellcode) are treated with `Severity.CRITICAL` / `BLOCK`.

---

## 2. Layout-Aware Extraction Pipeline (`PP-StructureV3`)

The extraction pipeline (`runtimes/document_intelligence/service/pipeline.py`) uses Baidu's PaddleX / PP-StructureV3 engine to convert documents into structured Markdown, tabular HTML, and high-resolution chart/table crops.

### Key Architectural Behaviors

#### 1. Image Crop vs. Text OCR
- Blocks labeled `table`, `figure`, `chart`, or `image` with a confidence score $\ge 0.70$ are extracted as discrete image crops (`ExtractedImage`).
- Stored crops are indexed for visual/multimodal search and referenced in the markdown using internal `cid:{id}` URIs instead of temporary server filesystem paths.
- OCR text within each crop is preserved alongside the image, enabling both lexical and visual search.

#### 2. GPU Batching Optimization
- By default, PP-StructureV3 initializes text recognition batch samplers with `batch_size = 1`. On GPU, this produces tiny sequential CUDA launches (sawtooth GPU load).
- On GPU, the service configures `ocr_batch_size` (default: 16) to batch text recognition across page regions.

#### 3. Table Structure Recognition (SLANet)
- SLANet parses table structures into structured HTML (cells, rows, spans), which is then converted into clean Markdown tables for text ingestion.

#### 4. Bounding Box Score Recovery
- PaddleX's `parsing_res_list` (reading-order content blocks) and `layout_det_res.boxes` (detector bounding boxes with confidence scores) are generated in the same pass but are not index-aligned. The pipeline recovers confidence scores by matching nearest bounding boxes.

#### 5. Memory Management
- Embedded PIL images in `markdown_images` are purged after extraction; only the compressed image bytes in `ExtractedImage.data` are retained to avoid OOM errors on large documents (e.g., 50+ pages).

---

## 3. Multimodal Embedding Proxy (`llama-embed`)

The multimodal embedding proxy (`runtimes/embedding_reranker/service/embedding.py`) proxies vector embedding requests to a `llama-server` sidecar running `Qwen3-VL-Embedding-2B`:

### 1. Token Budgeting & Image Downsampling
- Vision models produce roughly 1 token per $32 \times 32$ pixel patch ($\approx 1024 \text{ px/token}$).
- The service enforces a pixel ceiling (default: 1,000,000 px, or $\sim 977$ image tokens) to safely fit within GPU slot ceilings without truncation.

### 2. Request Protocol
- `llama-server` does not accept the standard OpenAI `image_url` object on `/embeddings`.
- The proxy structures the input payload as `{"prompt_string": ..., "multimodal_data": [base64, ...]}`, where `prompt_string` embeds the dynamic media-placeholder marker discovered via `GET /props`.

### 3. Vector Extraction
- With `--pooling last`, `llama-server` returns pooled vectors wrapped in a nested list: `data[0]["embedding"] = [[float, ...]]`. The proxy unwraps this to return a flat list of 2048 floats matching the database's `vector(2048)` column.
