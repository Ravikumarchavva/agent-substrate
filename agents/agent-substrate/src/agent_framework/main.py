from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.model_clients.openai.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.observability.telemetry import configure_opentelemetry, shutdown_opentelemetry
from agent_framework.configs.settings import settings
from agent_framework.messages import TextDeltaChunk, ReasoningDeltaChunk, CompletionChunk

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
        # Stream chunks as Server-Sent Events (SSE)
        async for chunk in agent.run_stream(user_input):
            try:
                if isinstance(chunk, TextDeltaChunk):
                    # Send incremental text as it arrives
                    payload = {
                        "type": "text_delta",
                        "content": chunk.text,
                        "partial": True
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                
                elif isinstance(chunk, ReasoningDeltaChunk):
                    # Send reasoning/thinking deltas (o1/o3 models)
                    payload = {
                        "type": "reasoning_delta",
                        "content": chunk.text,
                        "partial": True
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                
                elif isinstance(chunk, CompletionChunk):
                    # Send final complete message
                    message = chunk.message
                    payload = {
                        "type": "completion",
                        "role": message.role,
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments
                            } for tc in message.tool_calls
                        ] if message.tool_calls else None,
                        "finish_reason": message.finish_reason,
                        "usage": {
                            "prompt_tokens": message.usage.prompt_tokens,
                            "completion_tokens": message.usage.completion_tokens,
                            "total_tokens": message.usage.total_tokens
                        } if message.usage else None,
                        "partial": False,
                        "complete": True
                    }
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                
                else:
                    # Fallback for unknown chunk types
                    payload = {
                        "type": "unknown",
                        "content": str(chunk),
                        "partial": True
                    }
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                    
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, default=str)}\n\n"

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="localhost", 
        port=8001, 
        # ssl_keyfile=settings.ROOT_DIR / "ssl/localhost+2-key.pem", 
        # ssl_certfile=settings.ROOT_DIR / "ssl/localhost+2.pem"
    )
