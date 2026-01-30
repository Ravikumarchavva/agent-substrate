from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.observability.telemetry import configure_opentelemetry, shutdown_opentelemetry
from agent_framework.configs.settings import settings

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- STARTUP ----------
    configure_opentelemetry(service_name="agent-framework", otlp_trace_endpoint="localhost:4318")

    app.state.agent = ReActAgent(
        name="DemoBot",
        description="A helpful assistant.",
        model_client=OpenAIClient(
            model="gpt-4o-mini",
            api_key=settings.OPENAI_API_KEY,
        ),
        tools=[CalculatorTool(), GetCurrentTimeTool()],
        memory=UnboundedMemory(),
        max_iterations=5,
        verbose=True,
    )

    yield

    shutdown_opentelemetry()

app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

from pydantic import BaseModel

class ChatRequest(BaseModel):
    messages: list

@app.post("/chat")
async def chat(req: ChatRequest):
    agent = app.state.agent

    # Extract last user message only
    user_input = req.messages[-1]["content"]

    response = await agent.run(user_input)

    return {
        "role": "assistant",
        "content": response
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="localhost", 
        port=8000, 
        # ssl_keyfile=settings.ROOT_DIR / "ssl/localhost+2-key.pem", 
        # ssl_certfile=settings.ROOT_DIR / "ssl/localhost+2.pem"
    )
