"""Static AST safety classifier for code-interpreter code.

The model authors the code it runs (not an adversary), so a static pattern
scan reliably separates harmless exploratory analysis (pandas / matplotlib /
reads / saving files in the workspace) from genuinely dangerous operations
(shell/subprocess, network egress, file deletion, dynamic code execution,
writes outside the workspace). Exploratory code runs with no approval;
dangerous code gates with a human-readable summary — see
``LocalSandboxCodeInterpreterTool.classify_risk`` and the invoker's risk gate.

Deterministic, free, no LLM call — runs on every code-interpreter execution.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from substrate.kernel.tools.tools import ToolRisk
from substrate.logger import setup_logging

if TYPE_CHECKING:
    from substrate.kernel.llm.llm import LLMClient

logger = setup_logging()

_SUMMARY_SYSTEM_PROMPT = (
    "You review Python code a data-analysis agent is about to run in a "
    "sandbox, for a human who must approve it. In ONE short sentence, plainly "
    "state what the code does and why it may be risky (e.g. deleting files, "
    "network calls, running shell commands). No preamble, no markdown."
)

# Modules whose mere use implies a dangerous capability.
_NETWORK_MODULES = {
    "socket",
    "requests",
    "urllib",
    "urllib2",
    "httpx",
    "aiohttp",
    "http",
    "ftplib",
    "smtplib",
    "telnetlib",
    "paramiko",
}
_SHELL_MODULES = {"subprocess", "pty", "pexpect"}

# Paths a write is allowed to target without gating. Everything else absolute
# is treated as an out-of-workspace write.
_ALLOWED_WRITE_PREFIXES = ("/app/workspace", "/tmp", "./", "workspace/")

# Attribute-call signatures (module.attr) that delete files.
_DELETE_CALLS = {
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("shutil", "rmtree"),
}
# Attribute-call signatures that run shell/exec.
_EXEC_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "spawnv"),
}
# Bare builtins that execute dynamically-constructed code.
_DYNAMIC_BUILTINS = {"eval", "exec", "compile", "__import__"}


def _attr_chain(node: ast.AST) -> tuple[str, ...]:
    """Return the dotted attribute chain of an expression, e.g. ``os.path.join``
    → ``("os", "path", "join")``. Empty tuple if not a plain name/attr chain."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return tuple(parts)
    return ()


def _is_write_mode(mode: str) -> bool:
    return any(c in mode for c in ("w", "a", "x", "+"))


class _RiskVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.reasons: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _SHELL_MODULES:
                self.reasons.add("runs shell/subprocess commands")
            elif root in _NETWORK_MODULES:
                self.reasons.add("makes network connections")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".")[0]
        if root in _SHELL_MODULES:
            self.reasons.add("runs shell/subprocess commands")
        elif root in _NETWORK_MODULES:
            self.reasons.add("makes network connections")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        chain = _attr_chain(node.func)

        # Bare builtins: eval(...), exec(...), open(...)
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _DYNAMIC_BUILTINS:
                self.reasons.add("executes dynamically-constructed code")
            elif name == "open":
                self._check_open(node)

        # module.attr(...) signatures
        elif len(chain) >= 2:
            head, tail = chain[0], chain[-1]
            pair = (head, tail)
            if pair in _DELETE_CALLS:
                self.reasons.add("deletes files")
            elif pair in _EXEC_CALLS:
                self.reasons.add("runs shell/subprocess commands")
            elif head in _SHELL_MODULES:
                self.reasons.add("runs shell/subprocess commands")
            elif head in _NETWORK_MODULES:
                self.reasons.add("makes network connections")
            elif tail in {"unlink", "rmdir"} and head not in {"os", "shutil"}:
                # pathlib: Path(...).unlink() / .rmdir()
                self.reasons.add("deletes files")

        self.generic_visit(node)

    def _check_open(self, node: ast.Call) -> None:
        # open(path, mode): flag write-mode opens targeting an absolute path
        # outside the workspace/tmp. Reads and in-workspace writes are fine.
        mode = ""
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        if not _is_write_mode(mode):
            return
        if node.args and isinstance(node.args[0], ast.Constant):
            path = str(node.args[0].value)
            if path.startswith("/") and not path.startswith(_ALLOWED_WRITE_PREFIXES):
                self.reasons.add("writes files outside the workspace")


def classify_code(code: str) -> tuple[ToolRisk, list[str]]:
    """Classify code as SAFE or CRITICAL plus the reasons it was flagged.

    Unparseable code is SAFE — it cannot execute a dangerous operation (the
    sandbox will just raise a SyntaxError).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ToolRisk.SAFE, []

    visitor = _RiskVisitor()
    visitor.visit(tree)
    if not visitor.reasons:
        return ToolRisk.SAFE, []
    return ToolRisk.CRITICAL, sorted(visitor.reasons)


def templated_summary(reasons: list[str]) -> str:
    """A plain-language fallback summary from the flagged reasons, used when no
    LLM summarizer is available."""
    if not reasons:
        return ""
    if len(reasons) == 1:
        body = reasons[0]
    else:
        body = ", ".join(reasons[:-1]) + f", and {reasons[-1]}"
    return f"This code {body}."


async def _llm_summary(code: str, model_client: LLMClient) -> str:
    """One-sentence natural-language summary of what dangerous code does."""
    from substrate.kernel import ChatMessage, TextBlock
    from substrate.kernel.llm import GenerationOptions

    messages = [
        ChatMessage(role="user", content=[TextBlock(text=f"```python\n{code}\n```")])
    ]
    resp = await model_client.generate(
        messages, options=GenerationOptions(system_instructions=_SUMMARY_SYSTEM_PROMPT)
    )
    return " ".join(b.text for b in resp.content if isinstance(b, TextBlock)).strip()


async def classify_and_summarize(
    code: str, model_client: LLMClient | None = None
) -> tuple[ToolRisk, str | None]:
    """Hybrid classifier: static AST decision (fast, every call) plus, only on
    the CRITICAL path, an LLM-written summary for the approval card — falling
    back to a templated summary when no model client is available or the call
    fails. SAFE code returns ``(SAFE, None)`` and never touches the LLM.
    """
    risk, reasons = classify_code(code)
    if risk == ToolRisk.SAFE:
        return risk, None

    if model_client is not None:
        try:
            summary = await _llm_summary(code, model_client)
            if summary:
                return risk, summary
        except Exception as exc:  # never block approval on a summary failure
            logger.warning("code-risk summary LLM call failed: %s", exc)
    return risk, templated_summary(reasons)
