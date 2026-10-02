"""Purity of the contracts — what someone implementing a port depends on.

The contracts (``tests/_layout.py``) are the engine's boundary with the outside: ports plus the value types in
their signatures. They stay importable by anyone who implements a port, so they must stay free of I/O, of the
engine, and of any vendor. (Which concepts may import which, and the core's third-party set, are rows in
``tests/invariants/test_structure.py``.)
"""

from __future__ import annotations

import re

from tests._layout import REPO_ROOT, contract_files

# Vendor strings that must never appear in a contract.
# Schema shaping belongs in an integration; the contracts are provider-neutral.
_VENDOR_PATTERNS = [
    r"\bdefer_loading\b",
    r"\btool_search\b",
    r"gpt-",
    r"claude-",
    r"gemini-",
]


def _strip_docstrings(text: str) -> str:
    text = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
    text = re.sub(r"'''.*?'''", "", text, flags=re.DOTALL)
    return text


def test_contracts_have_no_vendor_strings() -> None:
    """Model names and provider-specific parameters belong in an integration. Any vendor pattern in a
    contract couples every port to one provider's API."""
    violations: list[str] = []
    for path in contract_files():
        stripped = _strip_docstrings(path.read_text(encoding="utf-8"))
        for pattern in _VENDOR_PATTERNS:
            for match in re.finditer(pattern, stripped):
                violations.append(
                    f"{path.relative_to(REPO_ROOT)}: contains vendor string {match.group()!r}"
                )
    assert not violations, (
        "Contracts must not contain vendor-specific strings:\n  "
        + "\n  ".join(violations)
    )


def test_contracts_import_only_pydantic_and_a_measured_stdlib_set() -> None:
    """A contract imports no runtime library or vendor SDK — only pydantic and a measured stdlib set.

    Imports of other contracts are fine; that they never reach into the engine is row I27.
    """
    # Deliberately the measured set, not a generous one: nothing here does I/O.
    # Adding a module is a decision to make in review, not a default.
    stdlib_prefixes = (
        "__future__",
        "base64",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "json",
        "re",
        "secrets",  # entropy for ids
        "threading",  # the id generator's monotonic counter
        "time",
        "typing",
        "uuid",
    )

    illegal: list[str] = []
    for path in contract_files():
        text = path.read_text(encoding="utf-8")
        stripped = _strip_docstrings(text)
        for line in stripped.splitlines():
            if not (line.startswith("import ") or line.startswith("from ")):
                continue
            if line.startswith("from __future__"):
                continue
            if "pydantic" in line or "typing_extensions" in line:
                continue
            if line.startswith("from substrate.") or line.startswith(
                "import substrate."
            ):
                continue
            if line.startswith("from .") or line.startswith("from .."):
                continue
            if any(
                line.startswith(f"import {prefix}") or line.startswith(f"from {prefix}")
                for prefix in stdlib_prefixes
            ):
                continue
            illegal.append(f"{path.relative_to(REPO_ROOT)}: {line.strip()}")
    assert not illegal, (
        "Contracts must stay at the protocol layer and use pydantic only for validation:\n  "
        + "\n  ".join(illegal[:20])
    )


def test_message_round_trip() -> None:
    """Message must serialize/deserialize cleanly via model_dump_json()."""
    from substrate.types import Actor
    from substrate.types import TextBlock, ChatMessage
    from substrate.runtime import Message, ChatPayload

    agent = Actor(type="agent", key="assistant")
    chat = ChatMessage(role="user", content=[TextBlock(text="hello")])
    payload = ChatPayload(message=chat)
    msg = Message(target=agent, payload=payload, sender=agent)

    json_str = msg.model_dump_json()
    restored = Message.model_validate_json(json_str)

    assert restored.id == msg.id
    assert restored.schema_version == 1
    assert isinstance(restored.payload, ChatPayload)
    assert restored.payload.message.role == "user"


def test_content_block_unknown_preserved() -> None:
    """Unknown block types must be preserved as UnknownBlock, not silently mangled."""
    from substrate.types import UnknownBlock, parse_content_block

    raw = {"type": "future_block_v99", "some_field": "some_value"}
    result = parse_content_block(raw, forward_compatible=True)  # type: ignore[arg-type]
    assert isinstance(result, UnknownBlock)
    assert result.raw["type"] == "future_block_v99"


def test_content_block_invalid_raises() -> None:
    """Invalid data for a known block type must raise BlockValidationError."""
    from substrate.types import BlockValidationError, parse_content_block

    bad = {"type": "text"}  # missing required 'text' field
    try:
        parse_content_block(bad)  # type: ignore[arg-type]
        assert False, "Should have raised BlockValidationError"
    except BlockValidationError:
        pass


def test_message_requires_sender() -> None:
    """Message must enforce non-anonymous provenance — omitting sender raises ValidationError."""
    import pytest
    from pydantic import ValidationError
    from substrate.types import Actor
    from substrate.runtime import Message, DataPayload

    target = Actor(type="agent", key="worker")

    # Missing sender
    with pytest.raises(ValidationError):
        Message(target=target, payload=DataPayload(data={}))  # type: ignore[call-arg]

    # Explicit None sender
    with pytest.raises(ValidationError):
        Message(target=target, sender=None, payload=DataPayload(data={}))  # type: ignore[arg-type]

    # Valid sender
    msg = Message(target=target, sender=Actor.user(), payload=DataPayload(data={}))
    assert msg.sender == Actor(type="user", key="default")


def test_actor_factory_helpers() -> None:
    """Actor factory classmethods must provide canonical standard addresses."""
    from substrate.types import Actor

    system_default = Actor.system()
    assert system_default == Actor(type="system", key="bootstrap")
    assert str(system_default) == "system/bootstrap"

    system_custom = Actor.system("cron")
    assert system_custom == Actor(type="system", key="cron")
    assert str(system_custom) == "system/cron"

    user_default = Actor.user()
    assert user_default == Actor(type="user", key="default")
    assert str(user_default) == "user/default"

    user_custom = Actor.user("alice")
    assert user_custom == Actor(type="user", key="alice")
    assert str(user_custom) == "user/alice"
