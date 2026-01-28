import asyncio
import os
from agent_framework.agents.react_agent import ReActAgent
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.observability.telemetry import configure_opentelemetry
from agent_framework.configs.settings import Settings

async def main():
    # 0. Configure Observability (OpenTelemetry) with Tempo
    # For Tempo, we use the HTTP OTLP endpoint (port 4318)
    # Traces: http://localhost:4318/v1/traces
    otlp_trace_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "localhost:4318")
    
    print(f"--- ReAct Agent Tempo Tracing Demo ---")
    print(f"Configuring OTLP HTTP exporter for traces at: {otlp_trace_endpoint}\n")
    
    configure_opentelemetry(
        service_name="agent-framework-tempo-demo",
        otlp_trace_endpoint=otlp_trace_endpoint
    )
    
    # 1. Initialize Tools
    tools = [
        CalculatorTool(),
        GetCurrentTimeTool()
    ]
    
    # 2. Initialize Client & Memory
    settings = Settings()
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        print("⚠️  Warning: OPENAI_API_KEY not found in environment.")
    
    client = OpenAIClient(model="gpt-4o", api_key=api_key)
    memory = UnboundedMemory()

    # 3. Initialize Agent
    agent = ReActAgent(
        name="TempoDemoBot",
        description="A helpful assistant for demonstrating tracing.",
        model_client=client,
        tools=tools,
        memory=memory,
        max_iterations=5,
        verbose=True
    )

    print(f"🤖 Agent '{agent.name}' initialized.")
    print("📝 Request: 'Calculate 123 * 456 and tell me the current time.'\n")

    # 4. Run Agent
    try:
        response = await agent.run("Calculate 123 * 456 and tell me the current time.")
        print(f"\n✅ Final Response: {response}")
    except Exception as e:
        print(f"\n❌ Execution Failed: {e}")

    # Give some time for BatchSpanProcessor to flush
    print("\nFlushing traces...")
    await asyncio.sleep(2)

    print("\nTraces should now be visible in Grafana at http://localhost:3000")
    print("Go to Explore -> Tempo and search for the 'agent-framework-tempo-demo' service.")

if __name__ == "__main__":
    asyncio.run(main())
