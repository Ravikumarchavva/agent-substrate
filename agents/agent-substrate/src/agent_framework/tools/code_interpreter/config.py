"""Configuration for the Firecracker code interpreter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CodeInterpreterConfig:
    """All tunables for the Firecracker code-interpreter service."""

    # ── Paths ────────────────────────────────────────────────────────────
    kernel_path: str = "/home/administrator/vmlinux-6.1.155"
    base_rootfs_path: str = "/tmp/ci-rootfs-update.ext4"

    # ── VM resources ─────────────────────────────────────────────────────
    vcpu_count: int = 1
    mem_size_mib: int = 256

    # ── Pool ─────────────────────────────────────────────────────────────
    pool_size: int = 2          # warm VMs kept ready
    pool_max_size: int = 8      # max concurrent VMs

    # ── Execution limits ─────────────────────────────────────────────────
    default_timeout: int = 30   # seconds
    max_timeout: int = 120      # hard ceiling
    max_code_size: int = 65_536 # bytes

    # ── vsock ────────────────────────────────────────────────────────────
    vsock_port: int = 52        # guest agent listens here
    vsock_guest_cid: int = 3    # CID 3+ (0=hypervisor, 1=reserved, 2=host)

    # ── Workspace ────────────────────────────────────────────────────────
    work_dir: str = "/tmp/firecracker-code-interpreter"

    # ── Sessions ─────────────────────────────────────────────────────────
    session_timeout: int = 1800     # seconds idle before VM is destroyed (30 min)
    max_sessions: int = 20          # max concurrent sessions

    # ── Guest agent ──────────────────────────────────────────────────────
    guest_agent_path: str = field(default="")

    def __post_init__(self) -> None:
        if not self.guest_agent_path:
            self.guest_agent_path = str(
                Path(__file__).parent / "guest_agent" / "agent.py"
            )
        Path(self.work_dir).mkdir(parents=True, exist_ok=True)
