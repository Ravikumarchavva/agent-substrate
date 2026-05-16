from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from .sandbox_service import (
    CodeInterpreterConfig,
    CodeInterpreterService,
    encode_text_file,
)
from .session_store import InMemorySessionStore, JsonSessionStore, SessionStore


_THREAD_ID: ContextVar[str | None] = ContextVar(
    "code_interpreter_thread_id",
    default=None,
)


def set_code_interpreter_thread_id(thread_id: str):
    """Bind tool calls in the current context to an application thread."""

    return _THREAD_ID.set(thread_id)


def reset_code_interpreter_thread_id(token: Any) -> None:
    _THREAD_ID.reset(token)


@contextmanager
def code_interpreter_thread(thread_id: str) -> Iterator[None]:
    token = set_code_interpreter_thread_id(thread_id)
    try:
        yield
    finally:
        reset_code_interpreter_thread_id(token)


class AgentSandboxTools:
    """Agent-facing wrapper that exposes one function: code_interpreter."""

    TOOL_NAME = "code_interpreter"

    def __init__(
        self,
        template: str = "python-sandbox-template",
        namespace: str = "default",
        *,
        default_thread_id: str = "default",
        session_store: SessionStore | None = None,
        session_store_path: str | os.PathLike[str] | None = None,
        service: CodeInterpreterService | None = None,
        request_timeout: int = 120,
        sandbox_ready_timeout: int = 180,
        server_port: int = 8888,
        shutdown_after_seconds: int | None = None,
        warmpool: str | None = None,
        raise_tool_errors: bool = False,
    ) -> None:
        if session_store is not None and session_store_path is not None:
            raise ValueError(
                "Pass either session_store or session_store_path, not both."
            )

        store = session_store
        if store is None and session_store_path is not None:
            store = JsonSessionStore(session_store_path)
        if store is None:
            store = InMemorySessionStore()

        config = CodeInterpreterConfig(
            template=template,
            namespace=namespace,
            request_timeout=request_timeout,
            sandbox_ready_timeout=sandbox_ready_timeout,
            server_port=server_port,
            shutdown_after_seconds=shutdown_after_seconds,
            warmpool=warmpool,
        )
        self.service = service or CodeInterpreterService(config=config, store=store)
        self.default_thread_id = default_thread_id
        self.raise_tool_errors = raise_tool_errors

    def code_interpreter(
        self,
        action: str,
        *,
        thread_id: str | None = None,
        code: str | None = None,
        language: str = "python",
        filename: str | None = None,
        content_base64: str | None = None,
        content: str | None = None,
        path: str | None = None,
        recursive: bool = False,
        overwrite: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Single function an agent can call for execution and file exchange."""

        try:
            normalized_action = self._normalize_action(action)
            resolved_thread_id = self._resolve_thread_id(thread_id)

            if normalized_action == "run_code":
                if language != "python":
                    raise ValueError("Only Python execution is currently supported.")
                if code is None:
                    raise ValueError("code is required for action='run_code'.")
                return self.service.run_code(resolved_thread_id, code, timeout=timeout)

            if normalized_action == "upload_file":
                if not filename:
                    raise ValueError("filename is required for action='upload_file'.")
                encoded_content = content_base64
                if encoded_content is None and content is not None:
                    encoded_content = encode_text_file(content)
                if encoded_content is None:
                    raise ValueError(
                        "content_base64 or content is required for action='upload_file'."
                    )
                return self.service.upload_file(
                    resolved_thread_id,
                    filename=filename,
                    content_base64=encoded_content,
                    overwrite=overwrite,
                    timeout=timeout,
                )

            if normalized_action == "download_file":
                if not path:
                    raise ValueError("path is required for action='download_file'.")
                return self.service.download_file(
                    resolved_thread_id,
                    path=path,
                    timeout=timeout,
                )

            if normalized_action == "list_files":
                return self.service.list_files(
                    resolved_thread_id,
                    path=path or ".",
                    recursive=recursive,
                    timeout=timeout,
                )

            if normalized_action == "reset_session":
                return self.service.reset_session(resolved_thread_id, timeout=timeout)

            if normalized_action == "status":
                return self.service.status(resolved_thread_id)

            if normalized_action == "terminate_session":
                return self.service.terminate_session(resolved_thread_id)

            if normalized_action == "list_sessions":
                return self.service.list_sessions()

            raise ValueError(f"Unsupported action: {action!r}")
        except Exception as exc:
            if self.raise_tool_errors:
                raise
            return {
                "status": "error",
                "action": action,
                "thread_id": thread_id or _THREAD_ID.get() or self.default_thread_id,
                "message": str(exc),
            }

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | str | None = None,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch an OpenAI-style tool call."""

        if name != self.TOOL_NAME:
            message = f"Unknown tool: {name!r}. Expected {self.TOOL_NAME!r}."
            if self.raise_tool_errors:
                raise ValueError(message)
            return {"status": "error", "message": message}

        args = self._parse_arguments(arguments)
        if thread_id is not None:
            args.setdefault("thread_id", thread_id)
        return self.code_interpreter(**args)

    def tool_definitions(self) -> list[dict[str, Any]]:
        """OpenAI-compatible schema with exactly one function tool."""

        return [
            {
                "type": "function",
                "function": {
                    "name": self.TOOL_NAME,
                    "description": (
                        "Stateful code interpreter for one conversation thread. "
                        "Use it to upload files, run Python, list generated files, "
                        "and download plots or other artifacts. Python variables "
                        "and files persist for the same thread_id."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": [
                                    "run_code",
                                    "upload_file",
                                    "download_file",
                                    "list_files",
                                    "reset_session",
                                    "status",
                                    "terminate_session",
                                ],
                                "description": "Operation to perform.",
                            },
                            "thread_id": {
                                "type": "string",
                                "description": (
                                    "Conversation/thread identifier. Host apps should "
                                    "inject this; omit when the wrapper is already "
                                    "bound to a thread."
                                ),
                            },
                            "code": {
                                "type": "string",
                                "description": "Python code for action='run_code'.",
                            },
                            "language": {
                                "type": "string",
                                "enum": ["python"],
                                "default": "python",
                            },
                            "filename": {
                                "type": "string",
                                "description": (
                                    "Destination filename for action='upload_file'. "
                                    "Relative paths are resolved inside the workspace."
                                ),
                            },
                            "content_base64": {
                                "type": "string",
                                "description": (
                                    "Base64-encoded bytes for action='upload_file'."
                                ),
                            },
                            "content": {
                                "type": "string",
                                "description": (
                                    "Plain text convenience content for upload_file. "
                                    "Use content_base64 for binary files."
                                ),
                            },
                            "path": {
                                "type": "string",
                                "description": (
                                    "Workspace path for download_file or list_files."
                                ),
                            },
                            "recursive": {
                                "type": "boolean",
                                "default": False,
                                "description": "Recursively list workspace files.",
                            },
                            "overwrite": {
                                "type": "boolean",
                                "default": True,
                                "description": "Allow upload_file to replace a file.",
                            },
                            "timeout": {
                                "type": "integer",
                                "minimum": 1,
                                "default": 120,
                                "description": "Request timeout in seconds.",
                            },
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    @staticmethod
    def _parse_arguments(arguments: dict[str, Any] | str | None) -> dict[str, Any]:
        if arguments is None:
            return {}
        if isinstance(arguments, str):
            return json.loads(arguments) if arguments.strip() else {}
        return dict(arguments)

    @staticmethod
    def _normalize_action(action: str) -> str:
        aliases = {
            "run": "run_code",
            "execute": "run_code",
            "execute_python": "run_code",
            "upload": "upload_file",
            "download": "download_file",
            "retrieve_file": "download_file",
            "list": "list_files",
            "reset": "reset_session",
            "stop": "terminate_session",
            "terminate": "terminate_session",
        }
        return aliases.get(action, action)

    def _resolve_thread_id(self, thread_id: str | None) -> str:
        candidate = (
            thread_id
            if thread_id is not None
            else _THREAD_ID.get() or self.default_thread_id
        )
        resolved = str(candidate)
        if not resolved.strip():
            raise ValueError("thread_id cannot be blank.")
        return resolved


CodeInterpreter = AgentSandboxTools

_default_lock = threading.RLock()
_default_tool: AgentSandboxTools | None = None


def configure_default_code_interpreter(**kwargs: Any) -> AgentSandboxTools:
    global _default_tool
    with _default_lock:
        _default_tool = AgentSandboxTools(**kwargs)
        return _default_tool


def get_default_code_interpreter() -> AgentSandboxTools:
    global _default_tool
    with _default_lock:
        if _default_tool is None:
            session_store_path = os.getenv("CODE_INTERPRETER_SESSION_STORE")
            _default_tool = AgentSandboxTools(
                template=os.getenv(
                    "CODE_INTERPRETER_TEMPLATE",
                    "python-sandbox-template",
                ),
                namespace=os.getenv("CODE_INTERPRETER_NAMESPACE", "default"),
                default_thread_id=os.getenv(
                    "CODE_INTERPRETER_DEFAULT_THREAD_ID",
                    "default",
                ),
                session_store_path=session_store_path,
            )
        return _default_tool


def code_interpreter(**kwargs: Any) -> dict[str, Any]:
    """Module-level convenience function for agent runtimes."""

    return get_default_code_interpreter().code_interpreter(**kwargs)
