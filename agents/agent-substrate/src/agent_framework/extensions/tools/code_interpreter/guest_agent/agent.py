#!/usr/bin/env python3
"""Guest Agent v3 — Persistent, multimodal code execution server.

Runs inside the Firecracker microVM.  Accepts multiple requests over
vsock port 52, maintaining full Python state between calls.

v3 additions over v2:
  - Structured ``outputs[]`` per response (text, image, error, file, stderr)
  - Auto-capture matplotlib figures as base64 PNG after each python exec
  - Binary file read/write (base64-encoded)
  - Backward-compatible: still returns flat output/stderr/error fields

Protocol: length-prefixed JSON  (4-byte big-endian length + JSON body)

Request types:
    python        exec(code) in persistent namespace + figure capture
    bash          /bin/bash -c cmd
    write_file    text file write
    read_file     text file read
    write_file_b  binary file write (base64-encoded content)
    read_file_b   binary file read  → base64-encoded content
    list_files    directory listing
    install       pip3 install packages
    get_state     list defined variables
    reset         clear namespace + temp files
    ping          liveness check
    shutdown      graceful poweroff
"""

import base64
import contextlib
import io
import json
import os
import socket
import struct
import subprocess
import time
import traceback

VSOCK_PORT = 52
MAX_OUTPUT = 1_000_000   # 1 MB cap per field
IDLE_TIMEOUT = 3600      # 1 h idle → auto-shutdown


# ── Wire protocol ────────────────────────────────────────────────────────────

def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 8192))
        if not chunk:
            raise ConnectionError("Connection closed unexpectedly")
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock):
    length = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if length > 32 * 1024 * 1024:
        raise ValueError(f"Message too large: {length} bytes")
    return json.loads(_recv_exact(sock, length))


def send_msg(sock, data):
    payload = json.dumps(data).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


# ── Guest Agent ──────────────────────────────────────────────────────────────

class GuestAgent:
    """Persistent Python execution engine with multimodal output."""

    def __init__(self):
        self.globals = {
            "__builtins__": __builtins__,
            "__name__": "__main__",
            "__doc__": "Firecracker Code Interpreter Session",
        }
        self.exec_count = 0
        self._running = True

    # ── Python execution ─────────────────────────────────────────────────

    def exec_python(self, code, timeout=30):
        """Execute Python with persistent state + auto-capture figures."""
        self.exec_count += 1
        cell_id = f"In[{self.exec_count}]"

        script_path = f"/tmp/exec_{self.exec_count:04d}.py"
        try:
            with open(script_path, "w") as f:
                f.write(f"# {cell_id}\n{code}\n")
        except OSError:
            pass

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        start = time.monotonic()
        success = True
        error = None

        try:
            with contextlib.redirect_stdout(stdout_buf), \
                 contextlib.redirect_stderr(stderr_buf):
                exec(compile(code, cell_id, "exec"), self.globals)
        except SystemExit as e:
            success = int(e.code or 0) == 0
            if not success:
                error = f"SystemExit({e.code})"
        except Exception:
            success = False
            error = traceback.format_exc()

        elapsed = round(time.monotonic() - start, 4)
        stdout_text = stdout_buf.getvalue()[:MAX_OUTPUT]
        stderr_text = stderr_buf.getvalue()[:MAX_OUTPUT]

        # Build structured outputs
        outputs = []
        if stdout_text:
            outputs.append({"type": "text", "content": stdout_text, "encoding": "utf-8"})
        if stderr_text:
            outputs.append({"type": "stderr", "content": stderr_text, "name": "stderr", "encoding": "utf-8"})

        # Auto-capture matplotlib figures
        outputs.extend(self._capture_figures())

        if error:
            outputs.append({"type": "error", "content": error, "encoding": "utf-8"})

        return {
            "success":        success,
            "outputs":        outputs,
            "output":         stdout_text,       # backward compat
            "stderr":         stderr_text,       # backward compat
            "error":          error,
            "execution_time": elapsed,
            "cell_id":        cell_id,
            "script_path":    script_path,
        }

    def _capture_figures(self):
        """Auto-save open matplotlib figures as base64 PNG."""
        captured = []
        try:
            plt = self.globals.get("plt") or self.globals.get("matplotlib", {})
            if not plt:
                # Check if matplotlib.pyplot was imported under any name
                for v in self.globals.values():
                    if hasattr(v, "get_fignums") and hasattr(v, "savefig"):
                        plt = v
                        break
            if not plt or not hasattr(plt, "get_fignums"):
                return captured

            fig_nums = plt.get_fignums()
            for num in fig_nums:
                try:
                    fig = plt.figure(num)
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
                    buf.seek(0)
                    b64 = base64.b64encode(buf.read()).decode("ascii")
                    captured.append({
                        "type": "image",
                        "content": b64,
                        "name": f"figure_{num}.png",
                        "format": "png",
                        "encoding": "base64",
                    })
                except Exception:
                    pass
            if fig_nums:
                plt.close("all")
        except Exception:
            pass
        return captured

    # ── Bash execution ───────────────────────────────────────────────────

    def exec_bash(self, cmd, timeout=30):
        """Run a bash command with full VM access."""
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, shell=True, executable="/bin/bash",
                capture_output=True, text=True, timeout=timeout, cwd="/tmp",
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
                    "HOME": "/root", "LANG": "C.UTF-8", "TMPDIR": "/tmp",
                },
            )
            stdout_text = proc.stdout[:MAX_OUTPUT]
            stderr_text = proc.stderr[:MAX_OUTPUT]
            outputs = []
            if stdout_text:
                outputs.append({"type": "text", "content": stdout_text, "encoding": "utf-8"})
            if stderr_text:
                outputs.append({"type": "stderr", "content": stderr_text, "name": "stderr", "encoding": "utf-8"})
            if proc.returncode != 0 and stderr_text:
                outputs.append({"type": "error", "content": stderr_text, "encoding": "utf-8"})
            return {
                "success":        proc.returncode == 0,
                "outputs":        outputs,
                "output":         stdout_text,
                "stderr":         stderr_text,
                "error":          stderr_text if proc.returncode != 0 else None,
                "exit_code":      proc.returncode,
                "execution_time": round(time.monotonic() - start, 4),
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False, "outputs": [{"type": "error", "content": f"Timed out after {timeout}s"}],
                "output": "", "stderr": "", "error": f"Bash timed out after {timeout}s",
                "exit_code": -1, "execution_time": float(timeout),
            }

    # ── File operations (text) ───────────────────────────────────────────

    def write_file(self, path, content):
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": path, "bytes_written": len(content)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_file(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_OUTPUT)
            return {"success": True, "path": path, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── File operations (binary, base64) ─────────────────────────────────

    def write_file_binary(self, path, b64_content):
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            data = base64.b64decode(b64_content)
            with open(path, "wb") as f:
                f.write(data)
            return {"success": True, "path": path, "bytes_written": len(data)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def read_file_binary(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read(MAX_OUTPUT)
            b64 = base64.b64encode(data).decode("ascii")
            return {
                "success": True, "path": path,
                "content": b64, "encoding": "base64", "size": len(data),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Directory listing ────────────────────────────────────────────────

    def list_files(self, path="/tmp"):
        try:
            entries = []
            for entry in sorted(os.scandir(path), key=lambda e: e.name):
                stat = entry.stat()
                entries.append({
                    "name": entry.name, "is_dir": entry.is_dir(), "size": stat.st_size,
                })
            return {"success": True, "path": path, "entries": entries}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Package installation ─────────────────────────────────────────────

    def install(self, packages):
        if not packages:
            return {"success": False, "error": "No packages specified"}
        safe = [p.strip() for p in packages if p.strip()]
        cmd = f"pip3 install --quiet --no-cache-dir {' '.join(safe)} 2>&1"
        return self.exec_bash(cmd, timeout=120)

    # ── Session state ────────────────────────────────────────────────────

    def get_state(self):
        skip = {"__builtins__", "__name__", "__doc__", "__loader__",
                "__spec__", "__package__", "__cached__", "__file__"}
        variables = {}
        for k, v in self.globals.items():
            if k.startswith("__") or k in skip:
                continue
            try:
                tname = type(v).__name__
                if hasattr(v, "shape"):
                    repr_str = f"{tname}{v.shape}"
                elif hasattr(v, "__len__"):
                    repr_str = f"{tname}[{len(v)}]"
                else:
                    repr_str = repr(v)[:120]
                variables[k] = {"type": tname, "repr": repr_str}
            except Exception:
                variables[k] = {"type": type(v).__name__, "repr": "<unprintable>"}

        scripts = sorted(
            f for f in os.listdir("/tmp") if f.startswith("exec_") and f.endswith(".py")
        )
        return {"success": True, "exec_count": self.exec_count,
                "variables": variables, "scripts": scripts}

    def reset(self):
        self.globals = {
            "__builtins__": __builtins__,
            "__name__": "__main__",
            "__doc__": "Firecracker Code Interpreter Session",
        }
        self.exec_count = 0
        for f in os.listdir("/tmp"):
            if f.startswith("exec_") and f.endswith(".py"):
                try:
                    os.unlink(os.path.join("/tmp", f))
                except OSError:
                    pass
        return {"success": True, "message": "Session state cleared"}

    # ── Request dispatcher ───────────────────────────────────────────────

    def handle(self, request):
        rtype = request.get("type", "python")
        try:
            if   rtype == "python":       return self.exec_python(request["code"], request.get("timeout", 30))
            elif rtype == "bash":         return self.exec_bash(request["cmd"], request.get("timeout", 30))
            elif rtype == "write_file":   return self.write_file(request["path"], request["content"])
            elif rtype == "read_file":    return self.read_file(request["path"])
            elif rtype == "write_file_b": return self.write_file_binary(request["path"], request["content"])
            elif rtype == "read_file_b":  return self.read_file_binary(request["path"])
            elif rtype == "list_files":   return self.list_files(request.get("path", "/tmp"))
            elif rtype == "install":      return self.install(request["packages"])
            elif rtype == "get_state":    return self.get_state()
            elif rtype == "reset":        return self.reset()
            elif rtype == "ping":         return {"success": True, "pong": True, "exec_count": self.exec_count}
            elif rtype == "shutdown":     self._running = False; return {"success": True, "shutdown": True}
            else:                         return {"success": False, "error": f"Unknown request type: {rtype!r}"}
        except Exception:
            return {"success": False, "error": traceback.format_exc()}

    # ── Server loop ──────────────────────────────────────────────────────

    def run(self):
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((socket.VMADDR_CID_ANY, VSOCK_PORT))
        sock.listen(16)
        sock.settimeout(IDLE_TIMEOUT)
        print(f"[agent-v3] Listening on vsock port {VSOCK_PORT}", flush=True)

        while self._running:
            try:
                conn, addr = sock.accept()
                try:
                    request = recv_msg(conn)
                    result  = self.handle(request)
                    send_msg(conn, result)
                except Exception as e:
                    try:
                        send_msg(conn, {"success": False, "error": str(e)})
                    except Exception:
                        pass
                finally:
                    conn.close()
            except socket.timeout:
                print(f"[agent-v3] Idle {IDLE_TIMEOUT}s, shutting down", flush=True)
                break
            except Exception as e:
                print(f"[agent-v3] Error: {e}", flush=True)
                break

        sock.close()
        print("[agent-v3] Powering off", flush=True)
        os.system("sync")
        os.system("poweroff -f")


if __name__ == "__main__":
    GuestAgent().run()
