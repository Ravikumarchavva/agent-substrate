"""Environment-based configuration for the Code Interpreter service.

All settings are read from environment variables with the ``CI_`` prefix.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class ServiceConfig(BaseSettings):
    """Code Interpreter service configuration."""

    # ── Firecracker paths ────────────────────────────────────────────────
    fc_kernel_path: str = "/data/vmlinux"
    fc_rootfs_path: str = "/data/rootfs.ext4"

    # ── VM resources ─────────────────────────────────────────────────────
    fc_vcpu_count: int = 1
    fc_mem_size_mib: int = 256

    # ── Warm pool ────────────────────────────────────────────────────────
    pool_size: int = 3
    pool_max_size: int = 16

    # ── Sessions ─────────────────────────────────────────────────────────
    session_timeout: int = 1800
    max_sessions: int = 50

    # ── Execution limits ─────────────────────────────────────────────────
    default_timeout: int = 30
    max_timeout: int = 300
    max_code_size: int = 1_000_000

    # ── vsock ────────────────────────────────────────────────────────────
    vsock_port: int = 52
    vsock_guest_cid_start: int = 3

    # ── Directories ──────────────────────────────────────────────────────
    work_dir: str = "/tmp/firecracker-vms"

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080

    # ── Inter-service auth ───────────────────────────────────────────────
    auth_token: str = ""

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "code-interpreter-0"

    model_config = {"env_prefix": "CI_"}
