"""readonly_kb_block() — the system-prompt note pointing the model at the
read-only /workspace/.kb mount (see BubblewrapRuntime._bwrap_argv() and
sandbox_service.py's per-user SandboxTemplate for what actually mounts it)."""

from __future__ import annotations

from substrate.serving.monolith.routes.chat_intents import readonly_kb_block


def test_empty_when_code_interpreter_has_no_workspace_access():
    assert readonly_kb_block(False) == ""


def test_mentions_the_mount_path_and_that_its_read_only():
    block = readonly_kb_block(True)
    assert "/workspace/.kb" in block
    assert "read-only" in block.lower()
