# 1 · Tools

## Three kinds of tool

The kernel defines three concrete types in `kernel/tools/tools.py`. Everything in L2 and L1 branches on this taxonomy.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef base    fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef local   fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef hosted  fill:#FFF3E0,stroke:#E65100,color:#BF360C
    classDef provdef fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef result  fill:#E3F2FD,stroke:#1565C0,color:#0D47A1

    ROOT["Tool Protocol · kernel/tools/tools.py<br/>name · description · input_schema · risk: ToolRisk"]:::base

    T1["Tool (LOCAL) — ToolInvoker runs execute() in-process<br/>execute(**kwargs, ctx) → ToolExecutionResult<br/>WebSearchTool · CalculatorTool · PostgresQueryTool · …"]:::local
    RES["ToolExecutionResult<br/>content: list[ContentBlock] · is_error<br/>structured_content: dict|None · app_data: dict|None"]:::result
    T2["HostedTool — provider executes (no local execute())<br/>provider_specs: list[ToolSpec], sent in LLM tools= array<br/>e.g. OpenAI code_interpreter · web_search"]:::hosted
    T3["ProviderDefinedTool — hybrid shape<br/>provider_specs → LLM call shape<br/>handle_call(**kwargs) → local side-effect<br/>e.g. ComputerUseTool · BashTool"]:::provdef

    ROOT --> T1
    ROOT --> T2
    ROOT --> T3
    T1 -->|"returns"| RES
    T2 ~~~ T3
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

    START(["App lifespan — create_app()"]):::out
    SCAN["CapabilityDiscovery.discover() · discovery.py<br/>walk 3 dirs (sorted, skip names starting with _):<br/>tools/ · tools/skills/ · tools/connectors/"]:::proc
    CHK{"per subdir — which marker file?<br/>tool.py · SKILL.md · connector.py"}:::dec
    LOAD["Load by type:<br/>tool.py → import_module + first class with<br/>{name, description, input_schema, execute}<br/>SKILL.md → SkillLoader._load_metadata (YAML only)<br/>connector.py → class named *Connector"]:::proc
    PKG["CatalogPackage<br/>name · path · components (tool · skill · connector)<br/>tool_class? · skill_metadata? · connector_class?"]:::out
    DEDUP{"name already found?<br/>first occurrence wins"}:::dec
    TOOLBOX["Toolbox<br/>add(tool_class) per tool · SkillManager.discover()<br/>registry.names() → list[str]"]:::out

    START --> SCAN --> CHK
    CHK -->|"matches"| LOAD --> PKG --> DEDUP
    CHK -->|"none → skip"| SCAN
    DEDUP -->|"new"| TOOLBOX
    DEDUP -->|"duplicate → skip"| SCAN
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
from agent_substrate.kernel.tools import ToolExecutionResult
from agent_substrate.kernel.core.content import TextBlock

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
from agent_substrate.kernel.tools import ToolRisk

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
