"""Gateway BFF — HTTP routes.

The Gateway routes client calls to internal services. It validates input,
checks policy, and composes responses. No business logic lives here.

Routes mirror the public API surface:
  POST /chat              → Workflow Orchestrator + Stream Projection
  GET  /chat/stream/{id}  → Stream Projection SSE
  POST /chat/respond/{id} → HITL Approval service
  POST /chat/{id}/cancel  → Workflow Orchestrator
  CRUD /threads           → Conversation service
  CRUD /threads/{id}/files → Artifact service
  POST /auth/*            → Identity Auth service
  POST /api/execute       → Code Interpreter service (direct execution)
  GET  /api/execute/health   → Code Interpreter health
  GET  /api/execute/sessions → Code Interpreter sessions
  GET  /health            → Local health check
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_framework.shared.contracts.conversation import (
    ChatRequest,
    ThreadCreate,
)
from agent_framework.shared.contracts.human_gate import HITLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gateway"])


def _get_auth_token(request: Request) -> str:
    """Extract raw Bearer token from request for proxying."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


# ── Auth routes (proxy to Identity service) ──────────────────────────────────

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/token")
async def exchange_token(request: Request):
    body = await request.json()
    client = request.app.state.identity_client
    try:
        return await client.exchange_token(body.get("frontend_token", ""))
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@auth_router.post("/refresh")
async def refresh_token(request: Request):
    body = await request.json()
    client = request.app.state.identity_client
    try:
        return await client.refresh_token(body.get("refresh_token", ""))
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@auth_router.get("/me")
async def get_me(request: Request):
    token = _get_auth_token(request)
    client = request.app.state.identity_client
    return await client.get_me(token)


# ── Thread routes (proxy to Conversation service) ───────────────────────────

thread_router = APIRouter(prefix="/threads", tags=["threads"])


@thread_router.post("", status_code=201)
async def create_thread(body: ThreadCreate, request: Request):
    token = _get_auth_token(request)
    client = request.app.state.conversation_client
    return await client.create_thread(token, name=body.name or "New Chat")


@thread_router.get("")
async def list_threads(
    request: Request,
    limit: int = 50,
    offset: int = 0,
):
    token = _get_auth_token(request)
    client = request.app.state.conversation_client
    return await client.list_threads(token, limit=limit, offset=offset)


@thread_router.get("/{thread_id}")
async def get_thread(thread_id: str, request: Request):
    token = _get_auth_token(request)
    client = request.app.state.conversation_client
    return await client.get_thread(token, thread_id)


@thread_router.delete("/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, request: Request):
    token = _get_auth_token(request)
    client = request.app.state.conversation_client
    await client.delete_thread(token, thread_id)


@thread_router.get("/{thread_id}/messages")
async def get_messages(thread_id: str, request: Request):
    token = _get_auth_token(request)
    client = request.app.state.conversation_client
    return await client.get_messages(token, thread_id)


# ── Chat routes (proxy to Workflow + Stream services) ────────────────────────

chat_router = APIRouter(tags=["chat"])


@chat_router.post("/chat")
async def chat(body: ChatRequest, request: Request):
    """Start a workflow run and return SSE stream from Stream Projection.

    The gateway:
    1. Validates the request and checks policy
    2. Sends the run command to Workflow Orchestrator
    3. Connects to Stream Projection SSE and proxies it to the client
    """
    token = _get_auth_token(request)

    # Check policy
    policy = request.app.state.policy_client
    try:
        allowed = await policy.check(token, "submit_conversation")
    except Exception:
        allowed = True  # graceful degradation: allow if policy service is down

    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorized to submit conversation")

    # Start workflow run
    workflow = request.app.state.workflow_client
    run_payload = {
        "thread_id": str(body.thread_id),
        "user_content": body.messages[-1].content if body.messages else "",
        "system_instructions": body.system_instructions,
        "file_ids": [str(f) for f in body.file_ids] if body.file_ids else [],
    }

    try:
        run_result = await workflow.start_run(token, run_payload)
    except Exception as e:
        logger.exception("Failed to start workflow run")
        raise HTTPException(status_code=502, detail=f"Workflow service error: {e}")

    run_id = run_result.get("run_id", "")

    # Connect to Stream Projection SSE
    stream_client = request.app.state.stream_client

    async def proxy_sse() -> AsyncIterator[str]:
        """Proxy SSE from Stream Projection to the client."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET",
                    f"{stream_client._base_url}/stream/{str(body.thread_id)}",
                    params={"run_id": run_id},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if await request.is_disconnected():
                            break
                        if line:
                            yield f"{line}\n"
                        else:
                            yield "\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        content=proxy_sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.post("/chat/respond/{request_id}")
async def respond_to_hitl(request_id: str, body: HITLResponse, request: Request):
    """Proxy HITL response to the HITL Approval service."""
    token = _get_auth_token(request)
    hitl = request.app.state.hitl_client
    return await hitl.respond(token, request_id, body.model_dump(exclude_none=True))


@chat_router.post("/chat/{thread_id}/cancel")
async def cancel_chat(thread_id: str, request: Request):
    """Proxy cancellation to the Workflow Orchestrator."""
    token = _get_auth_token(request)
    workflow = request.app.state.workflow_client
    return await workflow.cancel_run(token, thread_id)


@chat_router.get("/hitl/status/{thread_id}")
async def hitl_status(thread_id: str, request: Request):
    """Proxy HITL status check."""
    token = _get_auth_token(request)
    hitl = request.app.state.hitl_client
    return await hitl.get_status(token, thread_id)


# ── File routes (proxy to Artifact service) ──────────────────────────────────

file_router = APIRouter(prefix="/threads", tags=["files"])


@file_router.get("/{thread_id}/files")
async def list_files(thread_id: str, request: Request):
    token = _get_auth_token(request)
    artifact = request.app.state.artifact_client
    return await artifact.list_files(token, thread_id)


# ── Code Interpreter proxy routes ─────────────────────────────────────────────
# Allows frontend / notebooks to run code directly (outside agent loop).
# The agent always goes through Tool Executor; these routes are for the
# notebook UX and direct API consumers.

execute_router = APIRouter(prefix="/api/execute", tags=["code-interpreter"])


@execute_router.post("")
async def execute_code(request: Request):
    """Execute code in a Firecracker microVM session.

    Body mirrors CodeInterpreterService ExecuteRequest:
      session_id, code, exec_type (python|bash), timeout
    """
    ci = getattr(request.app.state, "code_interpreter_client", None)
    if ci is None:
        raise HTTPException(status_code=503, detail="Code interpreter service not configured")
    body = await request.json()
    session_id = body.get("session_id", "default")
    return await ci.execute(session_id, body)


@execute_router.get("/health")
async def execute_health(request: Request):
    """Health of all code interpreter pod(s)."""
    ci = getattr(request.app.state, "code_interpreter_client", None)
    if ci is None:
        raise HTTPException(status_code=503, detail="Code interpreter service not configured")
    return await ci.health()


@execute_router.get("/sessions")
async def execute_sessions(request: Request):
    """List active sessions on the code interpreter pod(s)."""
    ci = getattr(request.app.state, "code_interpreter_client", None)
    if ci is None:
        raise HTTPException(status_code=503, detail="Code interpreter service not configured")
    return await ci.list_sessions()


@execute_router.post("/sessions/{session_id}/reset")
async def reset_session(session_id: str, request: Request):
    """Reset (recreate) a VM session."""
    ci = getattr(request.app.state, "code_interpreter_client", None)
    if ci is None:
        raise HTTPException(status_code=503, detail="Code interpreter service not configured")
    return await ci.reset_session(session_id)


@execute_router.delete("/sessions/{session_id}", status_code=204)
async def destroy_session(session_id: str, request: Request):
    """Destroy a VM session and release VM resources."""
    ci = getattr(request.app.state, "code_interpreter_client", None)
    if ci is None:
        raise HTTPException(status_code=503, detail="Code interpreter service not configured")
    await ci.destroy_session(session_id)
