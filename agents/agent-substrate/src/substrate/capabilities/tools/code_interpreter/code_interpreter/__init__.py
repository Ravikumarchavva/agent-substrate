"""CodeInterpreter — executes code in sandboxed container environments
(Kubernetes agent-sandbox pods, or a locally-composed sandbox container)."""

from __future__ import annotations

from .agent_sandbox_tools import (
    AgentSandboxTools,
    code_interpreter,
    code_interpreter_thread,
    configure_default_code_interpreter,
    get_default_code_interpreter,
    reset_code_interpreter_thread_id,
    set_code_interpreter_thread_id,
)
from .k8s_tool import K8sSandboxCodeInterpreterTool
from .local_sandbox_tool import LocalSandboxCodeInterpreterTool
from .sandbox_service import CodeInterpreterConfig, CodeInterpreterService
from .session_store import InMemorySessionStore, JsonSessionStore, SandboxSession


def main() -> None:
    print("Hello from code-interpreter!")


__all__ = [
    "main",
    "AgentSandboxTools",
    "K8sSandboxCodeInterpreterTool",
    "LocalSandboxCodeInterpreterTool",
    "code_interpreter",
    "code_interpreter_thread",
    "configure_default_code_interpreter",
    "get_default_code_interpreter",
    "reset_code_interpreter_thread_id",
    "set_code_interpreter_thread_id",
    "CodeInterpreterConfig",
    "CodeInterpreterService",
    "InMemorySessionStore",
    "JsonSessionStore",
    "SandboxSession",
]
