import asyncio
from ravi.fabric.catalog import AgentCatalog
from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.fabric.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.fabric.memory.unbounded import UnboundedMemory
from ravi.shared.observability.telemetry import configure_opentelemetry
from ravi.fabric.runtime import LocalRuntime
from ravi.console import Console


async def main():
    # 0. Configure Observability (OpenTelemetry)
    configure_opentelemetry(service_name="react-agent-demo")

    print("--- ReAct Agent Observability Demo ---\n")

    # 1. Initialize Tools
    tools = [CalculatorTool(), GetCurrentTimeTool()]

    # For this demo, we use built-in tools (Calculator and GetCurrentTime)

    # 3. Initialize Client & Memory
    from ravi.configs.settings import settings

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        print(
            "⚠️  Warning: OPENAI_API_KEY not found in environment. Example might fail."
        )

    # 4. Initialize LocalRuntime
    runtime = LocalRuntime()
    await runtime.start()

    try:
        catalog = AgentCatalog()
        model_name = settings.CHAT_MODEL.split("/")[-1]
        catalog.register_model(
            "primary", OpenAIClient(model=model_name, api_key=api_key)
        )
        catalog.register_memory("memory", UnboundedMemory())
        for tool in tools:
            catalog.register_tool(tool)

        # 5. Initialize Agent
        agent = AssistantAgent(
            name="DemoBot",
            runtime=runtime,
            description="A helpful assistant for demonstration.",
            catalog=catalog,
            max_iterations=5,
            verbose=True,
        )
        await agent.start()

        print(f"🤖 Agent '{agent.name}' initialized with {len(tools)} tools.")
        print(
            "📝 Request: 'What is the square root of 256 multiplied by 14? Also what time is it?'\n"
        )

        # 6. Run Agent
        con = Console(agent)
        response = await con.run(
            "What is the square root of 256 multiplied by 14? Also what time is it?"
        )
        print(f"\n✅ Final Response: {response}")
    except Exception as e:
        print(f"\n❌ Execution Failed: {e}")
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
