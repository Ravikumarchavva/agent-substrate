"""Example 1-2: Core Contracts — typed kernel contracts, all offline, no external services."""

import asyncio
from uuid import uuid4

from ravi.fabric.catalog import AgentCatalog
from ravi.kernel.contracts import (
    CanonicalMessage,
    EventEnvelope,
    MessageRole,
    ToolCallRequest,
    ToolCallSpec,
    ToolExecutionResult,
)
from ravi.kernel.messages.client_messages import (
    ToolCallMessage,
    ToolExecutionResultMessage,
)
from ravi.kernel.messages.content import TextBlock
from ravi.kernel.plugin import get_registered, list_registered, register_tool
from ravi.kernel.tools.base_tool import BaseTool, ToolResult, ToolRisk


async def main() -> None:
    # --- 1. ToolCallRequest & ToolExecutionResult
    print("=== 1. ToolCallRequest & ToolExecutionResult ===")

    req = ToolCallRequest(
        name="calculator",
        arguments={"expression": "2 ** 10"},
        agent_name="DemoBot",
        step=3,
    )
    print(f"call_id : {req.call_id!r}   (auto-generated UUID)")
    print(f"name    : {req.name}")
    print(f"frozen  : {req.model_config.get('frozen')}  ← immutable after creation")

    result = ToolExecutionResult(
        call_id=req.call_id,
        name=req.name,
        content=[TextBlock(text="1024")],
        duration_ms=12.4,
    )
    print(f"result.text  → {result.text!r}")
    print(f"result.is_error → {result.is_error}")

    # --- 2. CanonicalMessage & MessageRole
    print("\n=== 2. CanonicalMessage & MessageRole ===")

    user_msg = CanonicalMessage(
        role=MessageRole.USER,
        content=[TextBlock(text="What is 2+2?")],
    )
    assistant_msg = CanonicalMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[
            ToolCallSpec(
                call_id="tc-001", name="calculator", arguments={"expression": "2+2"}
            )
        ],
    )
    tool_msg = CanonicalMessage(
        role=MessageRole.TOOL,
        content=[TextBlock(text="4")],
        tool_call_id="tc-001",
        name="calculator",
    )

    for m in [user_msg, assistant_msg, tool_msg]:
        tc = m.tool_calls[0].name if m.tool_calls else "-"
        text = m.content[0].text if m.content else "-"
        print(f"role={m.role.value:<10}  tool={tc:<12}  text={text!r}")

    # --- 3. tool_call_id canonicalization
    print("\n=== 3. tool_call_id canonicalization ===")

    tc_msg = ToolCallMessage(
        id="abc-123", name="calculator", arguments={"expression": "1+1"}
    )
    print(f"tc.id           → {tc_msg.id!r}")
    print(
        f"tc.tool_call_id → {tc_msg.tool_call_id!r}   ← canonical property alias for .id"
    )
    print(f"Same value?     → {tc_msg.id == tc_msg.tool_call_id}")

    tr_msg = ToolExecutionResultMessage(
        tool_call_id="abc-123",
        content=[TextBlock(text="2")],
    )
    print(f"tr.tool_call_id → {tr_msg.tool_call_id!r}")
    print(f"IDs match?      → {tc_msg.tool_call_id == tr_msg.tool_call_id}")

    # --- 4. AgentCatalog resource registry
    print("\n=== 4. AgentCatalog resource registry ===")

    from ravi.fabric.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
    from ravi.integrations.llm.openai.openai_client import OpenAIClient
    from ravi.fabric.memory.unbounded import UnboundedMemory

    from ravi.configs.settings import settings

    catalog = AgentCatalog()
    model_name = settings.CHAT_MODEL.split("/")[-1]
    catalog.register_model(
        "primary", OpenAIClient(model=model_name, api_key=settings.OPENAI_API_KEY)
    )
    catalog.register_memory("memory", UnboundedMemory())
    catalog.register_tool(CalculatorTool())
    catalog.register_tool(GetCurrentTimeTool())

    model = catalog.get_model("primary")
    mem = catalog.get_memory("default")
    tools = catalog.all_tools()
    print(f"get_model('primary')  → {type(model).__name__}")
    print(f"get_memory('default') → {type(mem).__name__}")
    print(f"all_tools()           → {[t.name for t in tools]}")

    # --- 5. plugin registry — @register_tool
    print("\n=== 5. Plugin registry — @register_tool ===")

    @register_tool("demo_echo")
    class EchoTool(BaseTool):
        def __init__(self) -> None:
            super().__init__(
                name="demo_echo",
                description="Echoes its input back.",
                risk=ToolRisk.SAFE,
            )

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(content=[TextBlock(text=str(kwargs))])

    registered_cls = get_registered("tool", "demo_echo")
    all_tool_names = list_registered("tool")
    print(f"get_registered('tool', 'demo_echo') → {registered_cls.__name__}")
    print(
        f"list_registered('tool') contains 'demo_echo': {'demo_echo' in all_tool_names}"
    )

    # --- 6. EventEnvelope
    print("\n=== 6. EventEnvelope ===")

    envelope: EventEnvelope[dict] = EventEnvelope[dict](
        event_type="agent.run.completed",
        payload={"run_id": str(uuid4()), "status": "ok"},
    )
    print(f"event_type     : {envelope.event_type!r}")
    print(f"event_id       : {envelope.event_id!r}")
    print(f"correlation_id : {envelope.correlation_id!r}")
    print(f"payload        : {envelope.payload}")


if __name__ == "__main__":
    asyncio.run(main())
#
# `ravi.kernel.contracts._tool` provides immutable, typed request/result objects
