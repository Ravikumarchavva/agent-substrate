"""Sandbox prelude — Python source injected into the sandbox as the first cell.

This module produces the prelude string that is executed at chain start to
set up the ``tools`` namespace, ``ToolResult`` handle, and ``artifacts``
helper inside the sandbox.

The prelude must be:
- stdlib-only (no external imports beyond ``requests``, already in the VM)
- self-contained (no substrate imports — runs inside the isolated VM)
- small (shipped on every chain run; latency-sensitive)

Design
------
``tools.<name>(**kwargs)``
    Async-compatible proxy that POSTs a ``ToolCallRequest`` JSON to the bridge
    endpoint (HTTP transport) or writes a control-channel marker to stdout.

``ToolResult`` handle
    ``result.text``       — inline text or preview when offloaded
    ``result.structured`` — structured_content dict
    ``result.ref``        — ArtifactStore ref (or None for inline results)
    ``result.files``      — list[ChainFile] for media outputs
    ``await result.materialize()`` — downloads the artifact to a local workspace
                                     file and returns the path (for large data)

``artifacts.put(path) -> ref``
    Upload a sandbox-produced file to the ArtifactStore and return its ref
    so it can be included in the chain's return value.

Pass-by-reference
    Passing a ``ToolResult`` with a non-None ``ref`` as an argument
    serialises to ``{"$artifact": ref}`` — the bridge resolves it server-side.
"""

from __future__ import annotations

import textwrap


def build_prelude(
    *,
    tool_names: list[str],
    bridge_url: str,
    chain_id: str,
    chain_token: str,
    workspace_dir: str = "/workspace",
) -> str:
    """Produce the prelude Python source string for a chain run.

    ``tool_names``   — list of tool names to expose in the ``tools`` namespace
    ``bridge_url``   — base URL of the bridge HTTP endpoint (empty string for
                       control-channel transport, which uses stdout markers)
    ``chain_id``     — unique ID for this chain run
    ``chain_token``  — single-use bearer token for the HTTP bridge
    ``workspace_dir``— sandbox workspace directory for materialised files
    """
    tool_list_repr = repr(tool_names)

    return textwrap.dedent(f"""
# ── chain prelude (auto-injected) ────────────────────────────────────────────
import json as _json
import os as _os
import uuid as _uuid

_BRIDGE_URL = {bridge_url!r}
_CHAIN_ID   = {chain_id!r}
_CHAIN_TOKEN = {chain_token!r}
_WORKSPACE   = {workspace_dir!r}
_TOOL_NAMES  = {tool_list_repr}

# ── ToolResult handle ─────────────────────────────────────────────────────────

class ToolResult:
    \"\"\"Handle for a tool invocation result. Pass as arg to pipe data by ref.\"\"\"
    def __init__(self, raw: dict):
        self.text       = raw.get("text", "")
        self.structured = raw.get("structured", {{}})
        self.ref        = raw.get("artifact_ref")
        self.files      = raw.get("files", [])
        self.status     = raw.get("status", "ok")

    def __repr__(self):
        if self.ref:
            return f"ToolResult(ref={{self.ref[:8]}}..., preview={{self.text[:60]!r}})"
        return f"ToolResult({{self.text[:80]!r}})"

    async def materialize(self) -> str:
        \"\"\"Download artifact to workspace disk and return local path.\"\"\"
        if self.ref is None:
            path = _os.path.join(_WORKSPACE, f"inline_{{_uuid.uuid4().hex[:8]}}.txt")
            with open(path, "w") as fh:
                fh.write(self.text)
            return path
        path = _os.path.join(_WORKSPACE, f"artifact_{{self.ref.replace('/', '_')}}")
        if not _os.path.exists(path):
            import requests as _req
            resp = _req.get(
                f"{{_BRIDGE_URL}}/chain/{{_CHAIN_ID}}/artifact/{{self.ref}}",
                headers={{"Authorization": f"Bearer {{_CHAIN_TOKEN}}"}},
                timeout=60,
            )
            resp.raise_for_status()
            with open(path, "wb") as fh:
                fh.write(resp.content)
        return path

    def _to_arg(self):
        \"\"\"Serialise handle for RPC arg (pass-by-ref).\"\"\"
        if self.ref:
            return {{"$artifact": self.ref}}
        return self.text

# ── artifacts helper ──────────────────────────────────────────────────────────

class _Artifacts:
    \"\"\"Upload local files to the ArtifactStore and get back a ref.\"\"\"

    def put(self, path: str, content_type: str = "application/octet-stream") -> str:
        import requests as _req
        with open(path, "rb") as fh:
            data = fh.read()
        resp = _req.post(
            f"{{_BRIDGE_URL}}/chain/{{_CHAIN_ID}}/artifact",
            headers={{
                "Authorization": f"Bearer {{_CHAIN_TOKEN}}",
                "Content-Type": content_type,
            }},
            data=data,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["ref"]

artifacts = _Artifacts()

# ── tools namespace ───────────────────────────────────────────────────────────

def _call_tool(name: str, kwargs: dict) -> ToolResult:
    \"\"\"Synchronous bridge call (wrapped in async proxy below).\"\"\"
    import requests as _req

    payload = {{
        "name": name,
        "arguments": {{
            k: (v._to_arg() if isinstance(v, ToolResult) else v)
            for k, v in kwargs.items()
        }},
        "call_id": str(_uuid.uuid4()),
    }}
    resp = _req.post(
        f"{{_BRIDGE_URL}}/internal/chain/{{_CHAIN_ID}}/invoke",
        headers={{
            "Authorization": f"Bearer {{_CHAIN_TOKEN}}",
            "Content-Type": "application/json",
        }},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    raw = resp.json()
    result = ToolResult(raw)
    if result.status == "error":
        raise RuntimeError(f"Tool '{{name}}' failed: {{result.text}}")
    if result.status == "denied":
        raise PermissionError(f"Tool '{{name}}' denied: {{result.text}}")
    return result

class _ToolProxy:
    def __init__(self, name: str):
        self._name = name
    def __call__(self, **kwargs):
        return _call_tool(self._name, kwargs)
    def __repr__(self):
        return f"<tool {{self._name!r}}>"

class _ToolsNamespace:
    def __getattr__(self, name: str) -> _ToolProxy:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in _TOOL_NAMES:
            raise AttributeError(
                f"No tool named {{name!r}}. Available: {{', '.join(sorted(_TOOL_NAMES))}}"
            )
        return _ToolProxy(name)

tools = _ToolsNamespace()
# ── end of chain prelude ──────────────────────────────────────────────────────
""").lstrip()


__all__ = ["build_prelude"]
