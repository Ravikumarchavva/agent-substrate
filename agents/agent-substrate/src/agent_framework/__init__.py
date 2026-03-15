"""agent_framework - Async AI-agent framework built on FastAPI.

Layer structure (dependencies flow downward only):
    core        - agents, memory, messages, context, guardrails (pure logic)
    providers   - LLM clients, audio clients, third-party APIs
    extensions  - tools, skills, MCP apps
    runtime     - HITL bridge, task store, credentials, telemetry
    server      - FastAPI app, routes, DB models

Recommended imports:
    from agent_framework.core.agents.react_agent import ReActAgent
    from agent_framework.providers.llm.openai.openai_client import OpenAIClient
    from agent_framework.extensions.tools.base_tool import BaseTool, ToolResult

See ARCHITECTURE.md for extension guides and the full layer diagram.
"""

from __future__ import annotations


def main() -> None:
    """Entry point - run uvicorn agent_framework.server.app:app to start."""
    print("agent-framework - run `uvicorn agent_framework.server.app:app --port 8001 --reload`")
