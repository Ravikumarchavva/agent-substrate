import asyncio
import os
from raavan.core.agents.react_agent import ReActAgent
from raavan.core.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from raavan.integrations.mcp import MCPClient
from raavan.integrations.llm.openai.openai_client import OpenAIClient
from raavan.core.memory.unbounded_memory import UnboundedMemory
from raavan.shared.observability.telemetry import configure_opentelemetry

async def main():
    # 0. Configure Observability (OpenTelemetry)
    configure_opentelemetry(service_name="react-agent-demo")
    
    print("--- ReAct Agent Observability Demo ---\n")

    # 1. Initialize Tools
    tools = [
        CalculatorTool(),
        GetCurrentTimeTool()
    ]
    
    # 2. Try to add MCP tools (optional)
    mcp_client = MCPClient()
    try:
        # Assuming npx is available and remote server is not the goal for this simple demo, 
        # but let's try a common one just in case headers or args are needed.
        # For this demo, we'll stick to built-ins to ensure it runs out-of-the-box
        # unless user has specific environment set up.
        pass
    except Exception as e:
        print(f"Skipping MCP tools: {e}")

    # 3. Initialize Client & Memory
    # Check for API Key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("⚠️  Warning: OPENAI_API_KEY not found in environment. Example might fail.")
        # Mocking for demonstration if key missing? 
        # No, better to fail loud or use a mock client if I had one.
    
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()

    # 4. Initialize Agent
    agent = ReActAgent(
        name="DemoBot",
        description="A helpful assistant for demonstration.",
        model_client=client,
        tools=tools,
        memory=memory,
        max_iterations=5,
        verbose=True # Enable verbose logging for "observability" demo
    )

    print(f"🤖 Agent '{agent.name}' initialized with {len(tools)} tools.")
    print("📝 Request: 'What is the square root of 256 multiplied by 14? Also what time is it?'\n")

    # 5. Run Agent
    try:
        response = await agent.run("What is the square root of 256 multiplied by 14? Also what time is it?")
        print(f"\n✅ Final Response: {response}")
    except Exception as e:
        print(f"\n❌ Execution Failed: {e}")

    # 6. Observability Proof
    # In a real app, logs would go to a file/service. Here they are printed to stderr via the logger configuration.

if __name__ == "__main__":
    asyncio.run(main())
