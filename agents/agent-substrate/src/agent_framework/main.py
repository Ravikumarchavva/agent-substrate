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
from agent_framework.messages.client_messages import ToolExecutionResultMessage
from agent_framework.human_input import AskHumanTool
from agent_framework.web_hitl import WebHITLBridge, _DONE

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import asyncio
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

    # Create the WebHITL bridge (shared across requests)
    bridge = WebHITLBridge(response_timeout=300.0)
    app.state.bridge = bridge

    # Create AskHumanTool wired to the bridge
    ask_tool = AskHumanTool(
        handler=bridge.human_handler,
        max_requests_per_run=5,
    )

    app.state.agent = ReActAgent(
        name="DemoBot",
        description="A helpful assistant.",
        model_client=OpenAIClient(
            model="gpt-5-mini",
            api_key=settings.OPENAI_API_KEY,
        ),
        tools=[ask_tool, CalculatorTool(), GetCurrentTimeTool()],
        system_instructions="""
        You are a helpful AI assistant. Use tools when needed.

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

        When you need user preferences or confirmation, use the ask_human tool
        to present options and let them choose.
        """,
        memory=UnboundedMemory(),
        max_iterations=5,
        verbose=True,
        # HITL: tool approval for specific tools
        tool_approval_handler=bridge.approval_handler,
        tools_requiring_approval=["calculator", "get_current_time"],
        # Set tool timeout to match HITL bridge timeout (5 minutes)
        tool_timeout=300.0,
    )

    yield

    shutdown_opentelemetry()

app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

from pydantic import BaseModel
from typing import Optional, Dict, Any

class ChatRequest(BaseModel):
    messages: list

class HITLResponse(BaseModel):
    """Response to a HITL request from the frontend."""
    # For tool approval
    action: Optional[str] = None  # "approve" | "deny" | "modify"
    modified_arguments: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    # For human input
    selected_key: Optional[str] = None
    selected_label: Optional[str] = None
    freeform_text: Optional[str] = None


@app.post("/chat")
async def chat(req: ChatRequest):
    agent: ReActAgent = app.state.agent
    bridge: WebHITLBridge = app.state.bridge

    # Extract last user message only
    user_input = req.messages[-1]["content"]

    # Merged queue: both agent chunks and HITL events flow through here
    merged_queue: asyncio.Queue = asyncio.Queue()

    async def agent_worker():
        """Run the agent stream and push chunks to the merged queue."""
        try:
            async for chunk in agent.run_stream(user_input):
                await merged_queue.put(("agent", chunk))
        except Exception as e:
            await merged_queue.put(("error", str(e)))
        finally:
            await merged_queue.put(("agent_done", None))

    async def hitl_worker():
        """Drain HITL events from the bridge and push to the merged queue."""
        while True:
            event = await bridge.get_event()
            if event is _DONE:
                break
            await merged_queue.put(("hitl", event))

    async def sse_generator():
        # Start both workers
        agent_task = asyncio.create_task(agent_worker())
        hitl_task = asyncio.create_task(hitl_worker())

        agent_finished = False

        try:
            while True:
                source, data = await merged_queue.get()

                if source == "agent":
                    chunk = data
                    try:
                        if isinstance(chunk, TextDeltaChunk):
                            payload = {
                                "type": "text_delta",
                                "content": chunk.text,
                                "partial": True
                            }
                            yield f"data: {json.dumps(payload)}\n\n"

                        elif isinstance(chunk, ReasoningDeltaChunk):
                            payload = {
                                "type": "reasoning_delta",
                                "content": chunk.text,
                                "partial": True
                            }
                            yield f"data: {json.dumps(payload)}\n\n"

                        elif isinstance(chunk, CompletionChunk):
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

                        elif isinstance(chunk, ToolExecutionResultMessage):
                            # Tool result — send to frontend for display
                            content_text = ""
                            if isinstance(chunk.content, list):
                                parts = []
                                for block in chunk.content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        parts.append(block.get("text", ""))
                                content_text = "\n".join(parts)
                            payload = {
                                "type": "tool_result",
                                "tool_name": getattr(chunk, "name", "unknown"),
                                "content": content_text,
                                "is_error": getattr(chunk, "isError", False),
                                "partial": False,
                            }
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

                        else:
                            payload = {
                                "type": "unknown",
                                "content": str(chunk),
                                "partial": True
                            }
                            yield f"data: {json.dumps(payload, default=str)}\n\n"

                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, default=str)}\n\n"

                elif source == "hitl":
                    # HITL event — forward directly to frontend
                    yield f"data: {json.dumps(data, default=str)}\n\n"

                elif source == "error":
                    yield f"data: {json.dumps({'type': 'error', 'error': data}, default=str)}\n\n"

                elif source == "agent_done":
                    agent_finished = True
                    # Signal the HITL worker to stop
                    await bridge.signal_done()
                    break

        finally:
            # Ensure tasks are cleaned up
            if not agent_task.done():
                agent_task.cancel()
            if not hitl_task.done():
                hitl_task.cancel()

    return StreamingResponse(
        content=sse_generator(),
        media_type="text/event-stream"
    )


@app.post("/chat/respond/{request_id}")
async def respond_to_hitl(request_id: str, resp: HITLResponse):
    """Resolve a pending HITL request (tool approval or human input)."""
    bridge: WebHITLBridge = app.state.bridge

    data = resp.model_dump(exclude_none=True)
    resolved = bridge.resolve(request_id, data)

    if not resolved:
        return {"status": "error", "message": f"No pending request with id={request_id}"}

    return {"status": "ok", "request_id": request_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="localhost",
        port=8000,
        # ssl_keyfile=settings.ROOT_DIR / "ssl/localhost+2-key.pem",
        # ssl_certfile=settings.ROOT_DIR / "ssl/localhost+2.pem"
    )
