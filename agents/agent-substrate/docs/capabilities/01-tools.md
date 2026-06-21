# 1 · Tools

## Three kinds of tool

The kernel defines three concrete types in `kernel/tools/tools.py`. Everything in L2 and L1 branches on this taxonomy.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart LR
    classDef base    fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef local   fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef hosted  fill:#FFF3E0,stroke:#E65100,color:#BF360C
    classDef provdef fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C

    ROOT["Tool Protocol\nkernel/tools/tools.py"]:::base

    subgraph LOCAL["LOCAL — runs in engine process"]
        style LOCAL fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
        T1["name, description\ninput_schema\nexecute(**kwargs) → ToolExecutionResult"]:::local
    end

    subgraph HOSTED["HOSTED — provider executes"]
        style HOSTED fill:#FFF3E0,stroke:#E65100,color:#BF360C
        T2["provider_specs: list[ToolSpec]\n(sent in tools= array, never execute())"]:::hosted
    end

    subgraph PROVDEF["PROVIDER_DEFINED — hybrid"]
        style PROVDEF fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
        T3["provider_specs → LLM calls it\nhandle_call(**kwargs) → local side-effect"]:::provdef
    end

    ROOT --> LOCAL
    ROOT --> HOSTED
    ROOT --> PROVDEF
```

| Type | Dispatch | Example |
|---|---|---|
| `Tool` (LOCAL) | `ToolInvoker` calls `execute()` in-process | `WebSearchTool`, `CalculatorTool` |
| `HostedTool` | Included in `tools=` array; provider runs it | Code execution on OpenAI |
| `ProviderDefinedTool` | LLM calls a provider-defined shape; `handle_call()` runs locally | `ComputerUseTool` |

Use `is_hosted_tool()` / `is_provider_defined_tool()` from `kernel.tools` to branch at dispatch.

## CapabilityDiscovery — auto-scan at startup

`CapabilityDiscovery` (`capabilities/tools/discovery.py`) walks three directories at boot:

- `capabilities/tools/` — tool packages (any subdirectory with `tool.py`)
- `capabilities/tools/skills/` — skill packages (any subdirectory with `SKILL.md`)
- `capabilities/tools/connectors/` — connector packages (any with `connector.py`)

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E3F2FD','primaryTextColor': '#0D47A1','primaryBorderColor': '#1565C0','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TD
    classDef proc fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef dec  fill:#FFF3E0,stroke:#E65100,color:#BF360C,font-weight:bold
    classDef out  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20

    START(["App lifespan starts"]):::out
    CD["CapabilityDiscovery()\n.discover()"]:::proc
    ITER["iterate subdirectories\nskip _ and __pycache__"]:::proc
    CHK_T{"tool.py\npresent?"}:::dec
    CHK_S{"SKILL.md\npresent?"}:::dec
    CHK_C{"connector.py\npresent?"}:::dec
    LOAD_T["importlib.import_module()\nfind class with\n{name,description,\ninput_schema,execute}"]:::proc
    LOAD_S["SkillLoader._load_metadata()\nparse YAML frontmatter"]:::proc
    LOAD_C["find class ending\nin *Connector"]:::proc
    PKG["CatalogPackage\n(name, path, components,\ntool_class, skill_metadata)"]:::out
    TOOLBOX["Toolbox.add(tool_class)\nfor each discovered tool"]:::out

    START --> CD --> ITER
    ITER --> CHK_T
    CHK_T -->|yes| LOAD_T --> PKG
    CHK_T -->|no| CHK_S
    CHK_S -->|yes| LOAD_S --> PKG
    CHK_S -->|no| CHK_C
    CHK_C -->|yes| LOAD_C --> PKG
    PKG --> TOOLBOX
```

First-occurrence wins — earlier directories take priority if the same package name appears in multiple locations.

## Built-in tools

| Package | Class | What it does |
|---|---|---|
| `web/search.py` | `WebSearchTool` | Tavily web search |
| `web/surfer.py` | `WebSurferTool` | Headless browser page fetch |
| `web/read_url.py` | `ReadUrlTool` | Fetch + extract text from URL |
| `web/wikipedia.py` | `WikipediaTool` | Wikipedia article lookup |
| `files/document_analyzer.py` | `DocumentAnalyzerTool` | Extract text from PDF/DOCX/etc |
| `files/invoice_extractor.py` | `InvoiceExtractorTool` | Structured invoice data extraction |
| `communication/email_sender.py` | `EmailSenderTool` | Send email via SMTP |
| `communication/http_request.py` | `HttpRequestTool` | Arbitrary HTTP requests |
| `compute/calculator.py` | `CalculatorTool` | Safe math expression evaluator |
| `database/postgres_query.py` | `PostgresQueryTool` | Run SQL on a user-configured DB |
| `ai/image_generator.py` | `ImageGeneratorTool` | Generate images via DALL-E / compatible |
| `ai/knowledge_search.py` | `KnowledgeSearchTool` | Search a `KnowledgeBase` via RAGPipeline |
| `task_manager/tool.py` | `TaskManagerTool` | Kanban board (create/update/list tasks) |
| `utils/current_time.py` | `CurrentTimeTool` | Current UTC timestamp |
| `utils/tool_search.py` | `ToolSearchTool` | Search the Toolbox by name/description |
| `code_interpreter/tool.py` | `CodeInterpreterTool` | Execute Python in Firecracker VM / K8s sandbox |
| `skills/tool.py` | `SkillTool` | Discover and activate agent skills |
| `chain/tool.py` | `ToolChainTool` | Script-driven multi-tool chaining (see page 3) |

## Writing a tool

Drop a `tool.py` in any subdirectory under `capabilities/tools/`:

```python
from ravi.kernel.tools import ToolExecutionResult
from ravi.kernel.core.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does — shown to the LLM"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    async def execute(self, *, ctx=None, query: str, **_) -> ToolExecutionResult:
        result = do_work(query)
        return ToolExecutionResult(content=[TextBlock(text=result)])
```

`CapabilityDiscovery` finds it automatically at next startup — no registration step needed.

### Risk annotation

Tag tools that modify state or call external APIs:

```python
from ravi.kernel.tools import ToolRisk

class DangerousTool:
    risk: ToolRisk = ToolRisk.HIGH   # SAFE | LOW | MEDIUM | HIGH | CRITICAL
    ...
```

`ToolInvoker` (L1) enforces approval gates for `HIGH` and `CRITICAL` tools before calling `execute()`.

### `ToolExecutionResult` fields

```python
@dataclass
class ToolExecutionResult:
    content: list[ContentBlock]       # TextBlock, ImageBlock, …
    is_error: bool = False
    structured_content: dict | None = None   # machine-readable output
    app_data: dict | None = None             # metadata not shown to LLM
```
