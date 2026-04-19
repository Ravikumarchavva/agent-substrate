from __future__ import annotations

from raavan.server.routes.chat import _configure_workspace_mail_request


class _Schema:
    def __init__(self, name: str) -> None:
        self.name = name


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_schema(self) -> _Schema:
        return _Schema(self.name)


def test_workspace_mail_request_prioritizes_google_workspace_tool() -> None:
    tools = [_Tool("ask_human"), _Tool("google_workspace"), _Tool("manage_tasks")]

    routed_tools, updated_instructions, tool_choice = _configure_workspace_mail_request(
        "can u summarize my recent 5 emails?",
        tools,
        "base instructions",
    )

    assert [tool.name for tool in routed_tools] == ["ask_human", "google_workspace"]
    assert "must call google_workspace" in updated_instructions
    assert tool_choice == "google_workspace"


def test_non_mail_request_keeps_original_tools_and_prompt() -> None:
    tools = [_Tool("ask_human"), _Tool("google_workspace"), _Tool("manage_tasks")]

    routed_tools, updated_instructions, tool_choice = _configure_workspace_mail_request(
        "what's the current time?",
        tools,
        "base instructions",
    )

    assert routed_tools == tools
    assert updated_instructions == "base instructions"
    assert tool_choice is None


def test_mail_request_without_google_workspace_tool_is_unchanged() -> None:
    tools = [_Tool("ask_human"), _Tool("manage_tasks")]

    routed_tools, updated_instructions, tool_choice = _configure_workspace_mail_request(
        "review my latest inbox emails",
        tools,
        "base instructions",
    )

    assert routed_tools == tools
    assert updated_instructions == "base instructions"
    assert tool_choice is None
