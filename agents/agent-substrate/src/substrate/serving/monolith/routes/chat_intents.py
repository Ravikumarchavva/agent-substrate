"""Chat intent-routing heuristics — task planning, workspace mail, calendar.

Split out of ``chat.py``: keyword/phrase-based detection of when the user's
message warrants forcing a particular tool (``manage_tasks``,
``google_workspace``) as the model's first call, since weaker models
sometimes ignore a plain system-prompt instruction to do so.
"""

from __future__ import annotations

import re
from typing import Any

ATTACHMENT_ANALYSIS_INSTRUCTIONS = (
    "When the user asks about attached files, images, or documents, inspect the "
    "attachment directly and answer in a normal assistant response. Avoid "
    "creating task lists, plans, or workflow-style tool loops unless the user "
    "explicitly asks for planning, task tracking, or automation. "
    "When presenting structured data, always use proper Markdown tables with "
    "pipe (|) syntax and header separator rows (|---|). Never use plain text "
    "or HTML tags like <br> for tabular data. "
    "Non-text, non-image attachments (PDFs, spreadsheets, etc.) are NOT "
    "automatically readable by you — you were not given a filesystem path to "
    "them unless one is explicitly listed above. Never guess or invent a path "
    "(e.g. '/mnt/data/...') and call code_interpreter on it; if no path was "
    "given, you cannot open the file. In that case, say plainly that you can't "
    "read this attachment's content directly, then offer to help once the user "
    "pastes the relevant text. Never fabricate or guess at a file's contents — "
    "answering with invented details is worse than admitting you can't read it."
)

ATTACHMENT_PLANNING_KEYWORDS = (
    "plan",
    "planning",
    "task",
    "tasks",
    "todo",
    "to-do",
    "checklist",
    "workflow",
    "steps",
    "roadmap",
    "organize",
    "organise",
)

# Stronger phrases that warrant forcing manage_tasks as the first tool call
# (model may ignore system prompt instructions on weaker models).
TASK_FORCE_PHRASES = (
    "plan tasks",
    "plan the tasks",
    "create tasks",
    "create a task",
    "task list",
    "todo list",
    "to-do list",
    "checklist for",
    "plan for",
    "plan a ",
    "plan the ",
    "plan to ",
    "plan my ",
    "organise tasks",
    "organize tasks",
    "break down",
    "break this down",
    "step by step plan",
    "steps to ",
    "steps for ",
    "roadmap for",
)

WORKSPACE_MAIL_NOUNS = (
    "email",
    "emails",
    "mail",
    "mails",
    "gmail",
    "inbox",
    "mailbox",
)

WORKSPACE_MAIL_ACTIONS = (
    "summarize",
    "summarise",
    "analyze",
    "analyse",
    "review",
    "check",
    "read",
    "scan",
    "show",
    "list",
)

WORKSPACE_MAIL_TOOL_NAMES = {"ask_human", "google_workspace"}

WORKSPACE_MAIL_INSTRUCTIONS = (
    "If the user asks about their Gmail, inbox, or recent emails and the "
    "google_workspace tool is available, you must call google_workspace before "
    "answering. Do not claim you lack inbox access and do not ask the user to "
    "paste emails until after the tool has been attempted. For requests about "
    "recent or latest emails, call google_workspace with service='gmail' and an "
    "empty query string, then summarize the five most recent relevant messages "
    "from the tool output unless the user asked for a different number."
)

WORKSPACE_CALENDAR_NOUNS = (
    "calendar",
    "event",
    "meeting",
    "appointment",
    "schedule",
    "reminder",
)

WORKSPACE_CALENDAR_WRITE_ACTIONS = (
    "create",
    "add",
    "schedule",
    "set up",
    "make",
    "book",
    "cancel",
    "delete",
    "remove",
)

WORKSPACE_CALENDAR_WRITE_INSTRUCTIONS = (
    "The user wants to create or cancel a calendar event using Google Calendar. "
    "Use the google_workspace tool with action='create_event' or action='cancel_event'. "
    "For create_event, provide title, start_time as ISO 8601 with timezone offset "
    "(e.g. '2026-04-20T19:00:00+05:30' for 7 PM IST), and optionally end_time. "
    "IST is UTC+05:30. If no end time is specified, end_time may be omitted (defaults to 1 hour after start). "
    "For cancel_event, you must first call google_workspace with service='calendar' "
    "to list events and find the event_id, then call again with action='cancel_event'."
)


def _tool_name(tool: Any) -> str:
    try:
        return tool.get_schema().name
    except Exception:
        return str(getattr(tool, "name", ""))


def _should_allow_task_planning(user_text: str) -> bool:
    normalized = user_text.lower()
    return any(keyword in normalized for keyword in ATTACHMENT_PLANNING_KEYWORDS)


def _should_force_task_planning(user_text: str) -> bool:
    """Return True when the request clearly asks to create a task list.

    Used to force manage_tasks as the initial tool call so weaker models
    don't ignore the system prompt instruction.
    """
    normalized = user_text.lower()

    # Explicit kanban/board request
    if "kanban" in normalized or "task board" in normalized:
        return True

    # Phrase-based match
    if any(phrase in normalized for phrase in TASK_FORCE_PHRASES):
        return True

    # Numbered task list pattern: "1. foo 2. bar 3. baz"
    # Matches when the user pastes/types a numbered list of 2+ items
    if len(re.findall(r"\b\d+[\.\)]\s+\S", user_text)) >= 2:
        return True

    return False


def _should_route_workspace_mail_request(user_text: str) -> bool:
    normalized = user_text.lower()
    if not any(keyword in normalized for keyword in WORKSPACE_MAIL_NOUNS):
        return False

    if any(keyword in normalized for keyword in WORKSPACE_MAIL_ACTIONS):
        return True

    return any(keyword in normalized for keyword in ("recent", "latest", "last "))


def _configure_workspace_mail_request(
    user_text: str,
    tools: list[Any],
    system_instructions: str,
) -> tuple[list[Any], str, str | None]:
    if not _should_route_workspace_mail_request(user_text):
        return tools, system_instructions, None

    if not any(_tool_name(tool) == "google_workspace" for tool in tools):
        return tools, system_instructions, None

    routed_tools = [
        tool for tool in tools if _tool_name(tool) in WORKSPACE_MAIL_TOOL_NAMES
    ]
    updated_instructions = (
        system_instructions
        + "\n\n---\n**Google Workspace mail instructions:**\n"
        + WORKSPACE_MAIL_INSTRUCTIONS
    )
    return routed_tools, updated_instructions, "google_workspace"


def _should_route_calendar_write_request(user_text: str) -> bool:
    normalized = user_text.lower()
    if not any(noun in normalized for noun in WORKSPACE_CALENDAR_NOUNS):
        return False
    return any(action in normalized for action in WORKSPACE_CALENDAR_WRITE_ACTIONS)


def _configure_calendar_write_request(
    user_text: str,
    tools: list[Any],
    system_instructions: str,
) -> tuple[list[Any], str, str | None]:
    if not _should_route_calendar_write_request(user_text):
        return tools, system_instructions, None

    if not any(_tool_name(tool) == "google_workspace" for tool in tools):
        return tools, system_instructions, None

    routed_tools = [
        tool for tool in tools if _tool_name(tool) in WORKSPACE_MAIL_TOOL_NAMES
    ]
    updated_instructions = (
        system_instructions
        + "\n\n---\n**Google Workspace calendar instructions:**\n"
        + WORKSPACE_CALENDAR_WRITE_INSTRUCTIONS
    )
    return routed_tools, updated_instructions, "google_workspace"


__all__ = [
    "ATTACHMENT_ANALYSIS_INSTRUCTIONS",
    "_tool_name",
    "_should_allow_task_planning",
    "_should_force_task_planning",
    "_should_route_workspace_mail_request",
    "_configure_workspace_mail_request",
    "_should_route_calendar_write_request",
    "_configure_calendar_write_request",
]
