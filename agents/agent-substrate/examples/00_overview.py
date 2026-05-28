# %%
# # Ravi Engine — Examples Overview
#
# All examples are organized into **8 folders**, each covering a distinct capability area.  
# Every notebook is self-contained with its own imports and can be run independently.
#
# ---
#
# ## Quick-start
#
# ```bash
# # 1. Install dependencies
# uv sync
#
# # 2. Start local infrastructure (Redis + Postgres)
# make infra-up
#
# # 3. Set your API key
# export OPENAI_API_KEY=sk-...
#
# # 4. Open any notebook in VS Code or Jupyter
# ```
#
# ---
#
# ## Folder Map
#
# | Folder | Topic | Notebooks |
# |---|---|---|
# | [`01_foundations/`](01_foundations/) | ReAct agent, core contracts | `01_react_agent`, `02_core_contracts` |
# | [`02_memory/`](02_memory/) | Memory backends, sliding window | `01_memory_backends`, `02_memory_system` |
# | [`03_mcp_tools/`](03_mcp_tools/) | MCP stdio, SSE, native tools, catalog adapter | `01_mcp_stdio`, `02_mcp_sse`, `03_mcp_native_tools`, `04_mcp_catalog_adapter` |
# | [`04_agents/`](04_agents/) | Combined tools, HITL, web surfer, multi-tenant | `01_combined_tools`, `02_hitl`, `03_web_surfer`, `04_multi_tenant` |
# | [`05_safety/`](05_safety/) | Guardrails, LLM-as-judge evals | `01_guardrails`, `02_evals` |
# | [`06_runtime/`](06_runtime/) | Actor runtime, internals, gRPC, Restate | `01_local_runtime`, `02_runtime_internals`, `03_grpc_runtime`, `04_restate_runtime` |
# | [`07_observability/`](07_observability/) | OpenTelemetry tracing, EventBus spans | `01_observability` |
# | [`08_deployment/`](08_deployment/) | Docker Compose, Kind K8s, invoice extractor, K8s code interpreter | `01_docker_services`, `02_kind_k8s`, `03_invoice_extractor`, `04_k8s_code_interpreter` |
#
# ---
#
# ## Infrastructure Requirements per Folder
#
# | Folder | Needs Redis | Needs Postgres | Needs K8s | Needs MCP Server | Offline OK |
# |---|---|---|---|---|---|
# | `01_foundations` | — | — | — | — | ✅ |
# | `02_memory` | 02 only | — | — | — | Partial |
# | `03_mcp_tools` | — | — | — | 01, 02, 04 | Partial |
# | `04_agents` | — | — | — | — | ✅ |
# | `05_safety` | — | — | — | — | ✅ |
# | `06_runtime` | — | — | 03 only | — | Partial |
# | `07_observability` | — | — | — | — | ✅ (no Tempo needed) |
# | `08_deployment` | ✅ | ✅ | 02, 04 | — | ❌ |
#
# ---
#
# ## Component Cheat-Sheet
#
# ### Build an agent
# ```python
# from ravi.extensions.agents.react.agent import ReActAgent
# from ravi.configs.settings import settings
# from ravi.integrations.llm.factory import create_model_client
# from ravi.kernel.agent_catalog import AgentCatalog
# from ravi.kernel.memory.unbounded_memory import UnboundedMemory
# from ravi.extensions.context.redis_model_context import UnboundedContext
# from ravi.extensions.tools.builtin_tools import CalculatorTool
#
# agent = ReActAgent(
#     name="MyAgent",
#     catalog=catalog,         # AgentCatalog with model + memory + tools
#     tools=[CalculatorTool()],
#     memory=UnboundedMemory(),
#     model_context=UnboundedContext(),
# )
# result = await agent.run("What is 2 ** 10?")
# ```
#
# ### Write a custom tool
# ```python
# from ravi.kernel.tools.base_tool import BaseTool, ToolResult, ToolRisk, HitlMode
# from ravi.kernel.messages.content import TextBlock
#
# class MyTool(BaseTool):
#     risk = ToolRisk.SAFE
#     hitl_mode = HitlMode.NONE
#
#     def __init__(self):
#         super().__init__(
#             name="my_tool",
#             description="Does something useful",
#             input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
#         )
#
#     async def execute(self, *, x: str) -> ToolResult:  # type: ignore[override]
#         return ToolResult(content=[TextBlock(text=f"Got: {x}")])
# ```
#
# ### Persist memory in Redis
# ```python
# from ravi.integrations.memory.redis_memory import RedisMemory  # ← integrations, not core!
#
# mem = RedisMemory(session_id="my-chat", redis_url="redis://localhost:6379/0")
# await mem.connect()
# await mem.restore()             # reload history
# await mem.add_message(msg)      # always await
# await mem.disconnect()          # NOT .close()
# ```
#
# ### Connect to an MCP server
# ```python
# from ravi.integrations.mcp import MCPClient
#
# client = MCPClient(url="http://localhost:9000/sse")
# tools = await client.discover_tools()   # returns list[MCPTool]
# ```
#
# ### Register in the AgentCatalog
# ```python
# from ravi.kernel.agent_catalog import AgentCatalog, ResourceSpec, ResourceType
#
# catalog = AgentCatalog()
# spec = ResourceSpec(name="my_tool", namespace="main.default", resource_type=ResourceType.TOOL)
# catalog.register(spec, my_tool_instance)
#
# # Retrieve by short name or FQN
# tool = catalog.get_tool("my_tool")          # short name
# tool = catalog.get_tool("main.default.my_tool")  # FQN
# ```
