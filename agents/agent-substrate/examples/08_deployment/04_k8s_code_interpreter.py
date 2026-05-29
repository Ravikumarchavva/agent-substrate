"""Example 08-4: CodeInterpreterTool — local fallback and k8s sandbox modes.

Demonstrates CodeInterpreterTool in two configurations:

  1. Local fallback mode — set CODE_INTERPRETER_URL to any URL; when the HTTP
     service is unreachable the tool automatically falls back to an in-process
     Python sandbox. Useful for local development and CI.

  2. K8s HTTP mode — set CODE_INTERPRETER_URL to the actual service URL inside
     a Kubernetes cluster. Each session gets a persistent Firecracker microVM
     pod where Python state survives across calls.

Sections:
  1. Tool setup — show configuration options
  2. Execute a simple Python snippet and capture stdout
  3. State persistence — variables survive between calls in the same session
  4. Error capture — syntax errors and runtime exceptions
  5. Bash execution — exec_type='bash' for shell commands
  6. K8s sandbox note — what changes in production

Note: k8s sandbox mode requires deployment/k8s/ manifests to be applied.
  kubectl apply -k deployment/k8s/overlays/local
"""

import asyncio
import json
import os

from ravi.catalog.tools.code_interpreter.tool import CodeInterpreterTool

# Infrastructure:
#   Local fallback (sections 2–5): no infra needed. Set CODE_INTERPRETER_URL
#     to any URL to enable HTTP mode with automatic local fallback.
#   K8s mode (section 6 note): requires the code-interpreter StatefulSet.
#     Set CODE_INTERPRETER_URL=http://code-interpreter:8080 inside the cluster.

# Trigger local fallback: point to a non-existent local server.
# When the HTTP request fails, the tool executes code in-process via exec().
os.environ.setdefault("CODE_INTERPRETER_URL", "http://localhost:9999")

# ---


def _parse_result(result_text: str) -> dict:
    """Parse the JSON payload from a ToolResult text block."""
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        return {"raw": result_text}


# ---


async def section_1_setup() -> CodeInterpreterTool:
    """Section 1 — Configure CodeInterpreterTool."""
    print("=== Section 1: Tool setup ===")

    # Auto-detects CODE_INTERPRETER_URL env var → HTTP mode with local fallback
    tool = CodeInterpreterTool()
    tool.session_id = "example-session-001"

    print(f"  Tool name   : {tool.name!r}")
    print(f"  Risk level  : {tool.risk.name}")
    print(
        f"  Mode        : http (CODE_INTERPRETER_URL={os.environ.get('CODE_INTERPRETER_URL')})"
    )
    print(f"  Session ID  : {tool.session_id}")
    print()
    print("  Modes available:")
    print("    http   — routes to code-interpreter service; falls back to local exec")
    print("    direct — uses a local SessionManager with Firecracker microVMs")
    print("    none   — no backend configured; all calls return an error")
    print()
    print("  exec_type options: 'python' (default), 'bash'")
    print()

    return tool


# ---


async def section_2_simple_execution(tool: CodeInterpreterTool) -> None:
    """Section 2 — Execute a simple Python snippet and capture stdout."""
    print("=== Section 2: Simple execution ===")

    result = await tool.execute(code="x = 6 * 7\nprint(f'Answer: {x}')")
    data = _parse_result(result.content[0].text)

    print(f"  is_error  : {result.is_error}")
    print(f"  success   : {data.get('success')}")
    print(
        f"  output    : {data.get('output', data.get('stdout', data.get('raw', '')))!r}"
    )
    print()


# ---


async def section_3_state_persistence(tool: CodeInterpreterTool) -> None:
    """Section 3 — Variables survive between calls in the same session."""
    print("=== Section 3: State persistence ===")

    # Call 1: define a variable
    await tool.execute(
        code="squares = [i**2 for i in range(1, 6)]\nprint('defined squares')"
    )

    # Call 2: reference the same variable — it is still in scope
    result2 = await tool.execute(code="print('squares:', squares)")
    data = _parse_result(result2.content[0].text)
    output = data.get("output", data.get("stdout", data.get("raw", "")))
    print(f"  Second call output: {output!r}")
    print(f"  State persisted: {'squares' in str(output)}")
    print()

    # Reset by switching sessions
    tool.session_id = "example-session-002"
    result3 = await tool.execute(code="print('new session'); x = 99\nprint('x =', x)")
    data3 = _parse_result(result3.content[0].text)
    print(
        f"  New session output: {data3.get('output', data3.get('stdout', data3.get('raw', '')))!r}"
    )
    tool.session_id = "example-session-001"  # restore
    print()


# ---


async def section_4_error_capture(tool: CodeInterpreterTool) -> None:
    """Section 4 — Syntax errors and runtime exceptions are returned, not raised."""
    print("=== Section 4: Error capture ===")

    # Syntax error
    result_syntax = await tool.execute(code="def broken(\npass")
    data = _parse_result(result_syntax.content[0].text)
    print(f"  Syntax error — is_error: {result_syntax.is_error}")
    print(f"    output: {str(data)[:120]}")

    # Runtime exception
    result_runtime = await tool.execute(
        code="raise ValueError('intentional error for demo')"
    )
    data2 = _parse_result(result_runtime.content[0].text)
    print(f"  Runtime error — is_error: {result_runtime.is_error}")
    print(f"    output: {str(data2)[:120]}")
    print()


# ---


async def section_5_bash_execution(tool: CodeInterpreterTool) -> None:
    """Section 5 — exec_type='bash' for shell commands."""
    print("=== Section 5: Bash execution ===")

    result = await tool.execute(
        code='echo hello && python3 -c "import sys; print(sys.version.split()[0])"',
        exec_type="bash",
    )
    data = _parse_result(result.content[0].text)
    output = data.get("output", data.get("stdout", data.get("raw", "")))
    print(f"  Bash output: {output!r}")
    print()


# ---


def section_6_k8s_note() -> None:
    """Section 6 — What changes in k8s production mode."""
    print("=== Section 6: K8s production mode ===")
    print()
    print("  In a Kubernetes cluster, point CODE_INTERPRETER_URL to the service:")
    print()
    print("    export CODE_INTERPRETER_URL=http://code-interpreter:8080")
    print()
    print("  Or pass an explicit HTTP client:")
    print()
    print(
        "    from ravi.catalog.tools.code_interpreter.http_client import CodeInterpreterClient"
    )
    print("    client = CodeInterpreterClient(base_url='http://code-interpreter:8080')")
    print("    tool = CodeInterpreterTool(http_client=client)")
    print()
    print(
        "  The service is a StatefulSet — each replica manages a Firecracker microVM pool."
    )
    print("  session_id maps to a specific VM; Python state survives across calls.")
    print()
    print("  Apply the manifests:")
    print("    kubectl apply -k deployment/k8s/overlays/local")
    print("    kubectl rollout status statefulset/code-interpreter -n agent-framework")
    print()
    print("  For the agent-sandbox (one pod per session) variant, see:")
    print("    src/ravi/catalog/tools/code_interpreter/code_interpreter/agent-sandbox/")


# ---


async def main() -> None:
    tool = await section_1_setup()
    await section_2_simple_execution(tool)
    await section_3_state_persistence(tool)
    await section_4_error_capture(tool)
    await section_5_bash_execution(tool)
    section_6_k8s_note()


if __name__ == "__main__":
    asyncio.run(main())
