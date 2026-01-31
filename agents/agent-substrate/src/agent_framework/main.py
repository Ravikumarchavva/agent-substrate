from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.model_clients.openai.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.observability.telemetry import configure_opentelemetry, shutdown_opentelemetry
from agent_framework.configs.settings import settings

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import json
import logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---------- STARTUP ----------
    configure_opentelemetry(service_name="agent-framework", otlp_trace_endpoint="localhost:4318")

    # Reduce noisy HTTP/SDK logs to avoid printing large JSON blobs to console
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    app.state.agent = ReActAgent(
        name="DemoBot",
        description="A helpful assistant.",
        model_client=OpenAIClient(
            model="gpt-4o-mini",
            api_key=settings.OPENAI_API_KEY,
        ),
        tools=[CalculatorTool(), GetCurrentTimeTool()],
        system_instructions="""
        You MUST format all math using Markdown LaTeX.

        Rules:
        - Inline math: $...$
        - Block math: $$...$$
        - Do NOT escape dollar signs
        - Do NOT use \\[ \\] or \\( \\)
        When the user asks for a table:
        - ALWAYS return a Markdown table
        - Use | pipes and a separator row
        - Never use aligned text or bullet lists
        - Do not explain the table before emitting it
        """,
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
    agent: ReActAgent = app.state.agent

    # Extract last user message only
    user_input = req.messages[-1]["content"]

    async def sse_generator():
        # Stream messages as Server-Sent Events (SSE)
        async for msg in agent.run_stream(user_input):
            try:
                if hasattr(msg, "to_dict"):
                    payload = msg.to_dict()
                elif hasattr(msg, "dict"):
                    payload = msg.dict()
                else:
                    payload = {"content": str(msg)}
                # Add a simple type hint for consumers
                payload["_type"] = type(msg).__name__
                if "role" not in payload and hasattr(msg, "role"):
                    payload["role"] = getattr(msg, "role")
                # Add streaming hints if available
                meta = getattr(msg, "metadata", {}) or {}
                payload["_partial"] = meta.get("partial", False)
                payload["_complete"] = meta.get("complete", False)
                # Ensure content is the chunk (if present)
                if hasattr(msg, "content") and msg.content is not None:
                    payload["content"] = msg.content
                # Prefix each SSE data frame
                yield f"data: {json.dumps(payload, default=str)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)}, default=str)}\n\n"

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="localhost", 
        port=8000, 
        # ssl_keyfile=settings.ROOT_DIR / "ssl/localhost+2-key.pem", 
        # ssl_certfile=settings.ROOT_DIR / "ssl/localhost+2.pem"
    )
