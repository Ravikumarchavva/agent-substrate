"""Gateway BFF Service — FastAPI application.

Entry point: uvicorn agent_framework.services.gateway.app:app --port 8001

The Gateway is the ONLY service exposed to the frontend. All client
requests go through here and are routed to internal services.
"""

from __future__ import annotations

import logging
import os
import json as _json
from contextlib import asynccontextmanager

from agent_framework.services.base import create_service_app
from agent_framework.services.gateway.clients import (
    ArtifactClient,
    CodeInterpreterServiceClient,
    ConversationClient,
    HITLClient,
    IdentityClient,
    PolicyClient,
    StreamClient,
    WorkflowClient,
)
from agent_framework.services.gateway.routes import (
    auth_router,
    chat_router,
    execute_router,
    file_router,
    router,
    thread_router,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Service URLs (from env or defaults for local dev)
    identity_url = os.environ.get("IDENTITY_SERVICE_URL", "http://localhost:8010")
    policy_url = os.environ.get("POLICY_SERVICE_URL", "http://localhost:8011")
    conversation_url = os.environ.get(
        "CONVERSATION_SERVICE_URL", "http://localhost:8012"
    )
    workflow_url = os.environ.get("WORKFLOW_SERVICE_URL", "http://localhost:8013")
    hitl_url = os.environ.get("HITL_SERVICE_URL", "http://localhost:8016")
    stream_url = os.environ.get("STREAM_SERVICE_URL", "http://localhost:8017")
    artifact_url = os.environ.get("ARTIFACT_SERVICE_URL", "http://localhost:8018")
    ci_service_url = os.environ.get("CODE_INTERPRETER_SERVICE_URL", "")

    # Initialize service clients
    clients = [
        ("identity_client", IdentityClient(identity_url)),
        ("policy_client", PolicyClient(policy_url)),
        ("conversation_client", ConversationClient(conversation_url)),
        ("workflow_client", WorkflowClient(workflow_url)),
        ("hitl_client", HITLClient(hitl_url)),
        ("stream_client", StreamClient(stream_url)),
        ("artifact_client", ArtifactClient(artifact_url)),
    ]

    for name, client in clients:
        await client.start()
        setattr(app.state, name, client)

    # Code interpreter client is optional — service only exists when CI is deployed
    if ci_service_url:
        ci_client = CodeInterpreterServiceClient(ci_service_url)
        await ci_client.start()
        app.state.code_interpreter_client = ci_client
        logger.info("Code Interpreter Service connected: %s", ci_service_url)
    else:
        app.state.code_interpreter_client = None
        logger.info("CODE_INTERPRETER_SERVICE_URL not set — /api/execute disabled")

    app.state.jwt_secret = os.environ.get(
        "JWT_SECRET",
        "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET",
    )

    logger.info("Gateway BFF started — routing to %d downstream services", len(clients))
    yield

    # Shutdown
    for name, client in clients:
        await client.close()
    if app.state.code_interpreter_client:
        await app.state.code_interpreter_client.close()



_cors_raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
_cors_origins = (
    _json.loads(_cors_raw)
    if _cors_raw
    else ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000"]
)

app = create_service_app(
    title="Gateway BFF",
    lifespan=lifespan,
    cors_origins=_cors_origins,
)
app.include_router(auth_router)
app.include_router(thread_router)
app.include_router(chat_router)
app.include_router(file_router)
app.include_router(execute_router)
app.include_router(router)
