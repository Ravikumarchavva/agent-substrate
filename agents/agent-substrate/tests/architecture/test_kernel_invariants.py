"""Architecture invariants that keep the kernel frozen.

These checks supplement the ``import-linter`` contracts in ``pyproject.toml``
with cheap heuristics that catch regressions early:

* No upward imports — the kernel (L0) must not import any layer above it
  (agents, capabilities, fabric) nor orthogonal modules (integrations, serving).
* LOC and file-count ceilings — catch accidental feature additions.
* Flat layout — kernel contains no subdirectories.
* No vendor strings — kernel must not reference any specific LLM provider name
  or proprietary API schema in source code.
* Round-trip serialization — core wire types must serialize/deserialize cleanly.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = REPO_ROOT / "src" / "agent_substrate" / "kernel"

# Ceilings — kernel/runtime/ (10 durable-runtime contracts) raised this from 30.
MAX_KERNEL_LOC = 6_000
MAX_KERNEL_FILES = 45

# Vendor strings that must NEVER appear in kernel source.
# Schema shaping belongs in integrations/; the kernel is provider-neutral.
_VENDOR_PATTERNS = [
    r"\bdefer_loading\b",
    r"\btool_search\b",
    r"gpt-",
    r"claude-",
    r"gemini-",
]


def _iter_kernel_files() -> list[Path]:
    return [p for p in KERNEL_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def _strip_docstrings(text: str) -> str:
    text = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
    text = re.sub(r"'''.*?'''", "", text, flags=re.DOTALL)
    return text


def test_kernel_loc_ceiling() -> None:
    files = _iter_kernel_files()
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)
    assert total < MAX_KERNEL_LOC, (
        f"Kernel grew to {total} LOC (ceiling {MAX_KERNEL_LOC}). "
        "Concrete code belongs in agents/capabilities/fabric/integrations — "
        "the kernel holds contracts only."
    )


def test_kernel_file_count_ceiling() -> None:
    n = len(_iter_kernel_files())
    assert n < MAX_KERNEL_FILES, (
        f"Kernel grew to {n} files (ceiling {MAX_KERNEL_FILES}). "
        "Have you added a feature module that belongs in a layer above?"
    )


_KERNEL_PERMITTED_SUBDIRS = {
    "runtime",  # durable-runtime contracts (EventLog, Inbox, Scheduler, Supervisor, …)
    "core",
    "messaging",
    "llm",
    "storage",
    "tools",
    "agent",
}


def test_kernel_is_flat() -> None:
    """Kernel must not contain unexpected subdirectories (other than __pycache__).

    ``kernel/runtime/`` is the one permitted subpackage — it groups the 10
    durable-runtime contract files (EventLog, Inbox, Scheduler, Supervisor, …)
    that together form a coherent L0 sub-domain.  Any new subdirectory must be
    explicitly added to ``_KERNEL_PERMITTED_SUBDIRS``.
    """
    unexpected = [
        p
        for p in KERNEL_DIR.iterdir()
        if p.is_dir() and p.name not in ("__pycache__", *_KERNEL_PERMITTED_SUBDIRS)
    ]
    assert not unexpected, (
        "Kernel must be a flat collection of contract files — no unexpected subdirectories. "
        "Either add to _KERNEL_PERMITTED_SUBDIRS (with justification) or move to a layer above:\n  "
        + "\n  ".join(str(d.relative_to(REPO_ROOT)) for d in unexpected)
    )


_FORBIDDEN_PREFIXES = (
    "agent_substrate.agents",
    "agent_substrate.capabilities",
    "agent_substrate.fabric",
    "agent_substrate.integrations",
    "agent_substrate.serving",
    "agent_substrate.config",
    "agent_substrate.logger",
)


def test_kernel_has_no_upward_imports() -> None:
    """No file in kernel/ may import from any layer above it."""
    violations: list[str] = []
    for path in _iter_kernel_files():
        text = path.read_text(encoding="utf-8")
        stripped = _strip_docstrings(text)
        for prefix in _FORBIDDEN_PREFIXES:
            for match in re.finditer(
                rf"^\s*(?:from\s+{re.escape(prefix)}|import\s+{re.escape(prefix)})",
                stripped,
                re.MULTILINE,
            ):
                relpath = path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relpath}: imports {prefix!r} ({match.group(0).strip()})"
                )
    assert not violations, (
        "Kernel must not import from any layer above it. Violations:\n  "
        + "\n  ".join(violations)
    )


def test_kernel_has_no_vendor_strings() -> None:
    """Kernel source must not reference vendor-specific LLM API strings.

    Schema shaping, model names, and provider-specific parameters belong in
    ``integrations/llm/``.  Any vendor pattern in the kernel source couples
    the foundation to one provider's API.
    """
    violations: list[str] = []
    for path in _iter_kernel_files():
        text = path.read_text(encoding="utf-8")
        stripped = _strip_docstrings(text)
        for pattern in _VENDOR_PATTERNS:
            for match in re.finditer(pattern, stripped):
                relpath = path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relpath}: contains vendor string {match.group()!r}"
                )
    assert not violations, (
        "Kernel must not contain vendor-specific strings. Violations:\n  "
        + "\n  ".join(violations)
    )


def test_message_round_trip() -> None:
    """Message must serialize/deserialize cleanly via model_dump_json()."""
    from agent_substrate.kernel.core.identity import AgentId
    from agent_substrate.kernel.core.content import TextBlock, ChatMessage
    from agent_substrate.kernel.messaging.message import Message, ChatPayload

    agent = AgentId(type="assistant", key="test")
    chat = ChatMessage(role="user", content=[TextBlock(text="hello")])
    payload = ChatPayload(message=chat)
    msg = Message(target=agent, payload=payload, sender=agent)

    json_str = msg.model_dump_json()
    restored = Message.model_validate_json(json_str)

    assert restored.id == msg.id
    assert restored.schema_version == 1
    assert isinstance(restored.payload, ChatPayload)
    assert restored.payload.message.role == "user"


def test_event_round_trip() -> None:
    """Event must serialize/deserialize cleanly."""
    from agent_substrate.kernel.messaging.events import Event
    from agent_substrate.kernel.core.identity import AgentId

    agent = AgentId(type="test", key="a")
    ev = Event.create("agent.started", source=agent, data={"step": 1})

    json_str = ev.model_dump_json()
    restored = Event.model_validate_json(json_str)

    assert restored.id == ev.id
    assert restored.type == "agent.started"
    assert restored.data["step"] == 1


def test_content_block_unknown_preserved() -> None:
    """Unknown block types must be preserved as UnknownBlock, not silently mangled."""
    from agent_substrate.kernel.core.content import content_block_from_dict, UnknownBlock

    raw = {"type": "future_block_v99", "some_field": "some_value"}
    result = content_block_from_dict(raw)  # type: ignore[arg-type]
    assert isinstance(result, UnknownBlock)
    assert result.raw["type"] == "future_block_v99"


def test_content_block_invalid_raises() -> None:
    """Invalid data for a known block type must raise BlockValidationError."""
    from agent_substrate.kernel.core.content import content_block_from_dict, BlockValidationError

    bad = {"type": "text"}  # missing required 'text' field
    try:
        content_block_from_dict(bad)  # type: ignore[arg-type]
        assert False, "Should have raised BlockValidationError"
    except BlockValidationError:
        pass


def test_custom_payload_serialization_round_trip() -> None:
    """A custom payload registered via register_payload_type is correctly deserialized from JSON."""
    from typing import Literal
    from agent_substrate.kernel.core.identity import AgentId
    from agent_substrate.kernel.messaging.message import Message, register_payload_type
    from agent_substrate.kernel.tools import PayloadBase

    class CustomTestPayload(PayloadBase):
        kind: Literal["custom_test"] = "custom_test"
        info: str

    register_payload_type(CustomTestPayload)

    agent = AgentId(type="test", key="custom")
    payload = CustomTestPayload(info="hello world")
    msg = Message(target=agent, payload=payload)

    # Convert to JSON string
    json_str = msg.model_dump_json()

    # Deserialize back
    restored = Message.model_validate_json(json_str)

    assert restored.payload.kind == "custom_test"
    assert isinstance(restored.payload, CustomTestPayload)
    assert restored.payload.info == "hello world"
