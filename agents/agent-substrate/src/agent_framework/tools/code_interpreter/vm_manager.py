"""Firecracker VM manager with warm pool and vsock communication.

Manages the full lifecycle:
  1. Copy/overlay rootfs
  2. Launch Firecracker via its REST API
  3. Communicate with guest agent over vsock
  4. Tear down VM after use
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from .config import CodeInterpreterConfig

logger = logging.getLogger(__name__)


# ── Data types ───────────────────────────────────────────────────────────────

class VMState(Enum):
    CREATING = auto()
    READY = auto()
    BUSY = auto()
    STOPPING = auto()
    DEAD = auto()


@dataclass
class VM:
    """Represents a single Firecracker microVM instance."""
    vm_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: VMState = VMState.CREATING
    socket_path: str = ""
    rootfs_path: str = ""
    work_dir: str = ""
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    vsock_uds_path: str = ""  # host-side UDS for vsock
    cid: int = 3
    created_at: float = field(default_factory=time.monotonic)

    def __repr__(self) -> str:
        return f"VM(id={self.vm_id}, state={self.state.name})"


# ── vsock communication ─────────────────────────────────────────────────────

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from a socket."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 8192))
        if not chunk:
            raise ConnectionError("Connection closed while reading")
        buf.extend(chunk)
    return bytes(buf)


def send_vsock_message(sock: socket.socket, data: dict) -> None:
    """Send length-prefixed JSON over a socket."""
    payload = json.dumps(data).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def recv_vsock_message(sock: socket.socket) -> dict:
    """Receive length-prefixed JSON from a socket."""
    raw_len = _recv_exact(sock, 4)
    msg_len = struct.unpack(">I", raw_len)[0]
    if msg_len > 10 * 1024 * 1024:
        raise ValueError(f"Response too large: {msg_len}")
    raw_msg = _recv_exact(sock, msg_len)
    return json.loads(raw_msg.decode("utf-8"))


# ── VM Manager ───────────────────────────────────────────────────────────────

class VMManager:
    """Manages the lifecycle of a single Firecracker VM."""

    def __init__(self, config: CodeInterpreterConfig):
        self.config = config

    async def create_vm(self, cid: int = 3) -> VM:
        """Boot a new Firecracker VM and wait until guest agent is ready."""
        vm = VM(cid=cid)
        vm.work_dir = tempfile.mkdtemp(
            prefix=f"fc-{vm.vm_id}-", dir=self.config.work_dir
        )
        vm.socket_path = os.path.join(vm.work_dir, "firecracker.sock")
        vm.rootfs_path = os.path.join(vm.work_dir, "rootfs.ext4")
        vm.vsock_uds_path = os.path.join(vm.work_dir, f"vsock_{self.config.vsock_port}.sock")

        logger.info("Creating VM %s (CID=%d)", vm.vm_id, cid)

        # 1. Copy rootfs
        await asyncio.to_thread(
            shutil.copy2, self.config.base_rootfs_path, vm.rootfs_path
        )

        # 2. Start Firecracker process
        vm.process = subprocess.Popen(
            ["firecracker", "--api-sock", vm.socket_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=vm.work_dir,
        )

        # Wait for API socket
        for _ in range(30):
            if os.path.exists(vm.socket_path):
                break
            await asyncio.sleep(0.1)
        else:
            vm.state = VMState.DEAD
            raise RuntimeError(f"VM {vm.vm_id}: Firecracker socket not created")

        await asyncio.sleep(0.1)

        # 3. Configure via Firecracker REST API
        try:
            await self._fc_put(vm, "boot-source", {
                "kernel_image_path": self.config.kernel_path,
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off quiet loglevel=1",
            })

            await self._fc_put(vm, "drives/rootfs", {
                "drive_id": "rootfs",
                "path_on_host": vm.rootfs_path,
                "is_root_device": True,
                "is_read_only": False,
            })

            await self._fc_put(vm, "machine-config", {
                "vcpu_count": self.config.vcpu_count,
                "mem_size_mib": self.config.mem_size_mib,
            })

            # vsock device — guest_cid must be >= 3
            await self._fc_put(vm, "vsock", {
                "guest_cid": cid,
                "uds_path": vm.vsock_uds_path,
            })

            # Start the VM
            await self._fc_put(vm, "actions", {"action_type": "InstanceStart"})

        except Exception:
            await self.destroy_vm(vm)
            raise

        vm.state = VMState.READY
        logger.info("VM %s is READY", vm.vm_id)
        return vm

    async def execute_code(self, vm: VM, code: str, timeout: int = 30) -> dict:
        """Send code to the guest agent and get the result via vsock."""
        return await self.execute_request(vm, {"type": "python", "code": code, "timeout": timeout}, timeout)

    async def execute_request(self, vm: VM, request: dict, timeout: int | None = None) -> dict:
        """Send any request to the guest agent and return the result.

        This is the low-level method used by SessionManager.  The VM state
        transitions READY → BUSY → READY so the same VM can be reused across
        multiple calls (one call per session request).
        """
        if vm.state not in (VMState.READY, VMState.BUSY):
            raise RuntimeError(f"VM {vm.vm_id} not available (state={vm.state.name})")

        _timeout = timeout or request.get("timeout", self.config.default_timeout)
        vm.state = VMState.BUSY

        try:
            result = await asyncio.to_thread(self._send_request, vm, request, _timeout)
            return result
        finally:
            # VM stays READY for the next request in this session
            if vm.state == VMState.BUSY:
                vm.state = VMState.READY

    def _send_request(self, vm: VM, request: dict, timeout: int) -> dict:
        """Blocking vsock communication with the guest agent.

        Firecracker exposes vsock as a Unix domain socket on the host.
        The host connects to the UDS and sends the Firecracker handshake
        ``CONNECT <port>\n``, then sends/receives length-prefixed JSON.
        """
        # Firecracker creates a UDS at: <uds_path>_<port>
        # We connect to it and that's tunneled to the guest's vsock port
        uds_connect_path = vm.vsock_uds_path

        # Wait for guest agent to be ready (the UDS is created by Firecracker
        # once the vsock device is active, but the guest agent may not be
        # listening yet)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout + 10)

        connected = False
        deadline = time.monotonic() + min(timeout, 30)
        last_err = None

        while time.monotonic() < deadline:
            try:
                sock.connect(uds_connect_path)
                # Firecracker vsock handshake: send "CONNECT <port>\n"
                sock.sendall(f"CONNECT {self.config.vsock_port}\n".encode())
                # Read response — expect "OK <port>\n"
                response = b""
                while b"\n" not in response:
                    chunk = sock.recv(256)
                    if not chunk:
                        raise ConnectionError("No handshake response")
                    response += chunk
                resp_str = response.decode().strip()
                if resp_str.startswith("OK"):
                    connected = True
                    break
                else:
                    raise ConnectionError(f"vsock handshake failed: {resp_str}")
            except (ConnectionRefusedError, FileNotFoundError, ConnectionError) as e:
                last_err = e
                try:
                    sock.close()
                except Exception:
                    pass
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(timeout + 10)
                time.sleep(0.5)

        if not connected:
            sock.close()
            raise ConnectionError(
                f"VM {vm.vm_id}: Could not connect to guest agent "
                f"via vsock after {timeout}s: {last_err}"
            )

        try:
            # Forward the full request dict to the guest agent
            send_vsock_message(sock, request)

            # Receive result
            result = recv_vsock_message(sock)
            return result

        except socket.timeout:
            return {
                "success": False,
                "output": "",
                "stderr": "",
                "error": f"Guest agent did not respond within {timeout}s",
                "execution_time": 0,
            }
        finally:
            sock.close()

    async def destroy_vm(self, vm: VM) -> None:
        """Kill the Firecracker process and clean up its work directory."""
        if vm.state == VMState.DEAD:
            return

        vm.state = VMState.STOPPING
        logger.info("Destroying VM %s", vm.vm_id)

        if vm.process and vm.process.poll() is None:
            vm.process.terminate()
            try:
                await asyncio.to_thread(vm.process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                vm.process.kill()
                await asyncio.to_thread(vm.process.wait, timeout=2)

        # Clean up work directory
        if vm.work_dir and os.path.isdir(vm.work_dir):
            await asyncio.to_thread(shutil.rmtree, vm.work_dir, True)

        vm.state = VMState.DEAD

    async def _fc_put(self, vm: VM, endpoint: str, payload: dict) -> str:
        """PUT to the Firecracker API via curl."""
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "curl", "-s",
                "--unix-socket", vm.socket_path,
                "-X", "PUT",
                f"http://localhost/{endpoint}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"VM {vm.vm_id}: fc_put({endpoint}) failed: {result.stderr}"
            )
        # Firecracker returns 204 with empty body on success, or JSON error
        if result.stdout.strip():
            resp = json.loads(result.stdout)
            if "fault_message" in resp:
                raise RuntimeError(
                    f"VM {vm.vm_id}: fc_put({endpoint}): {resp['fault_message']}"
                )
        return result.stdout


# ── VM Pool ──────────────────────────────────────────────────────────────────

class VMPool:
    """Async pool of warm Firecracker VMs for low-latency code execution.

    On ``start()``, pre-boots ``config.pool_size`` VMs.
    ``acquire()`` returns a ready VM; ``release()`` destroys it and spawns
    a replacement in the background.
    """

    def __init__(self, config: CodeInterpreterConfig | None = None):
        self.config = config or CodeInterpreterConfig()
        self.manager = VMManager(self.config)
        self._pool: asyncio.Queue[VM] = asyncio.Queue(
            maxsize=self.config.pool_max_size
        )
        self._cid_counter: int = self.config.vsock_guest_cid
        self._started = False
        self._replenish_tasks: list[asyncio.Task] = []
        self._lock = asyncio.Lock()

    @property
    def available(self) -> int:
        return self._pool.qsize()

    def _next_cid(self) -> int:
        """Allocate a unique guest CID (must be >= 3)."""
        self._cid_counter += 1
        return self._cid_counter

    async def start(self) -> None:
        """Pre-boot the warm pool."""
        if self._started:
            return
        self._started = True
        logger.info(
            "Starting VM pool (size=%d, max=%d)",
            self.config.pool_size,
            self.config.pool_max_size,
        )

        tasks = [self._create_and_enqueue() for _ in range(self.config.pool_size)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = sum(1 for r in results if not isinstance(r, Exception))
        logger.info("VM pool started: %d/%d VMs ready", successes, self.config.pool_size)

    async def stop(self) -> None:
        """Drain and destroy all VMs in the pool."""
        self._started = False

        # Cancel replenish tasks
        for t in self._replenish_tasks:
            t.cancel()
        self._replenish_tasks.clear()

        # Destroy all queued VMs
        destroyed = 0
        while not self._pool.empty():
            try:
                vm = self._pool.get_nowait()
                await self.manager.destroy_vm(vm)
                destroyed += 1
            except asyncio.QueueEmpty:
                break
        logger.info("VM pool stopped: destroyed %d VMs", destroyed)

    async def acquire(self, timeout: float = 60.0) -> VM:
        """Get a ready VM from the pool."""
        try:
            vm = await asyncio.wait_for(self._pool.get(), timeout=timeout)
            if vm.state != VMState.READY or (vm.process and vm.process.poll() is not None):
                # VM died while waiting — destroy and try again
                await self.manager.destroy_vm(vm)
                self._schedule_replenish()
                return await self.acquire(timeout=timeout)
            return vm
        except asyncio.TimeoutError:
            raise RuntimeError(
                "No VM available in pool — all VMs are busy. "
                f"Pool size: {self.config.pool_size}, max: {self.config.pool_max_size}"
            )

    async def release(self, vm: VM) -> None:
        """Destroy a used VM and schedule a replacement."""
        await self.manager.destroy_vm(vm)
        if self._started:
            self._schedule_replenish()

    def _schedule_replenish(self) -> None:
        """Kick off background VM creation to keep the pool warm."""
        task = asyncio.create_task(self._create_and_enqueue())
        self._replenish_tasks.append(task)

        def _on_done(t: asyncio.Task) -> None:
            try:
                self._replenish_tasks.remove(t)
            except ValueError:
                pass

        task.add_done_callback(_on_done)

    async def _create_and_enqueue(self) -> None:
        """Create a VM and put it in the pool."""
        try:
            cid = self._next_cid()
            vm = await self.manager.create_vm(cid=cid)
            await self._pool.put(vm)
        except Exception as e:
            logger.error("Failed to create warm VM: %s", e, exc_info=True)
            raise

    async def execute(self, code: str, timeout: int | None = None) -> dict:
        """High-level: acquire VM → execute → release → return result."""
        timeout = min(timeout or self.config.default_timeout, self.config.max_timeout)

        if len(code.encode("utf-8")) > self.config.max_code_size:
            return {
                "success": False,
                "output": "",
                "stderr": "",
                "error": f"Code exceeds maximum size of {self.config.max_code_size} bytes",
                "execution_time": 0,
            }

        vm = await self.acquire()
        try:
            result = await self.manager.execute_code(vm, code, timeout)
            return result
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "stderr": "",
                "error": f"{type(e).__name__}: {str(e)}",
                "execution_time": 0,
            }
        finally:
            await self.release(vm)
