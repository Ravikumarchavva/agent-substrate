"""ravi — async AI-agent framework.

Layer structure (dependencies flow downward only):
    kernel  — contracts, routing primitives, content blocks
    fabric  — runtime services: catalog, context, lifecycle, middleware,
              resources, supervision
    integrations — LLM clients, MCP, external adapters
    server  — FastAPI monolith
    services — FastAPI microservices
"""

from __future__ import annotations


def main() -> None:
    """Entry point — run ``uvicorn ravi.server.app:app --port 8001 --reload``."""
    print("ravi — run `uvicorn ravi.server.app:app --port 8001 --reload`")
