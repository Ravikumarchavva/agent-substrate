"""Firecracker-based secure code interpreter for AI agents.

Provides hardware-level isolation via Firecracker microVMs.
Sessions are persistent: each conversation thread keeps the same VM
and its Python state until a 30-minute idle timeout expires.
"""

from .tool import CodeInterpreterTool
from .http_client import CodeInterpreterClient
from .vm_manager import VMManager, VMPool
from .session_manager import SessionManager, SessionInfo
from .config import CodeInterpreterConfig

__all__ = [
    "CodeInterpreterTool",
    "CodeInterpreterClient",
    "SessionManager",
    "SessionInfo",
    "VMManager",
    "VMPool",
    "CodeInterpreterConfig",
]
