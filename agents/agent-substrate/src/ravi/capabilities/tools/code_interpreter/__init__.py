"""Code interpreter tools for AI agents.

Two deployment targets:

* **CodeInterpreterTool** — Firecracker microVM isolation.  Local dev and
  standalone VM deployments.  Requires a privileged pod with nested
  virtualisation support.

* **K8sSandboxCodeInterpreterTool** — Kubernetes agent-sandbox (kubernetes-sigs).
  One pod-per-session via CRD; no privileged pods required.  Preferred for
  Kind / EKS / GKE cluster deployments.
"""

from .tool import CodeInterpreterTool
from .http_client import CodeInterpreterClient
from .vm_manager import VMManager, VMPool
from .session_manager import SessionManager, SessionInfo
from .config import CodeInterpreterConfig
from .code_interpreter import K8sSandboxCodeInterpreterTool

__all__ = [
    "CodeInterpreterTool",
    "K8sSandboxCodeInterpreterTool",
    "CodeInterpreterClient",
    "SessionManager",
    "SessionInfo",
    "VMManager",
    "VMPool",
    "CodeInterpreterConfig",
]
