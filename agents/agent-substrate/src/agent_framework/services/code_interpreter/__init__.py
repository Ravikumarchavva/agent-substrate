"""Code Interpreter microservice — standalone FastAPI app.

Runs Firecracker microVMs with persistent sessions for AI agents.
Deploy as a separate k3s pod with privileged access to /dev/kvm.

Quickstart::

    uvicorn agent_framework.code_interpreter_service.app:app \\
        --host 0.0.0.0 --port 8080 --workers 1

Environment variables (all prefixed with ``CI_``)::

    CI_FC_KERNEL_PATH    Path to vmlinux kernel image
    CI_FC_ROOTFS_PATH    Path to rootfs ext4 image
    CI_POOL_SIZE         Warm VM pool size (default 3)
    CI_MAX_SESSIONS      Max concurrent sessions (default 50)
    CI_AUTH_TOKEN        Shared secret for inter-service auth (optional)
"""
