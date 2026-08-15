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
    "If the user's request could plausibly be 'based on' an attached file "
    "(e.g. 'create a report/summary/analysis'), the attachment itself is the "
    "answer — do not call ask_human to confirm this; retrieve its content and "
    "proceed. "
    "When presenting structured data, always use proper Markdown tables with "
    "pipe (|) syntax and header separator rows (|---|). Never use plain text "
    "or HTML tags like <br> for tabular data. "
    "PDF/DOCX/PPTX attachments are NOT inlined into this prompt — they were "
    "indexed into a searchable knowledge base scoped to this conversation. "
    "To read one, call knowledge_search with action='search' and a query "
    "describing what you need (e.g. the user's question) — do not assume "
    "you already know its contents. For a broad request like 'summarize "
    "this document' or 'what is this about', issue AT LEAST 2-3 "
    "knowledge_search calls with different, specific queries (e.g. one per "
    "likely section/topic/date range) and a limit of 10-15, not one vague "
    "query with the default limit — a single narrow search under-covers a "
    "multi-page or multi-section document. Once you have the results, state "
    "the specific names, dates, and facts they contain directly and "
    "confidently — do not hedge with 'appears to be' / 'I can infer' / "
    "'this seems to' when the retrieved text plainly says so, and do not "
    "offer to 'extract more detail' as a follow-up — if a fuller answer is "
    "possible with more searches, just do them now instead of asking. "
    "A knowledge_search result IS the real content of the attached file — the "
    "actual extracted text of that exact page, not a title, a reference, or a "
    "placeholder standing in for content you don't really have. If a call "
    "returned passages, you have the document; proceed to use them directly. "
    "Do not tell the user the file 'isn't accessible' or you only have "
    "'the title, not the contents' when you have already received real "
    "passages from it in this same turn — that is never true, and asking the "
    "user to re-upload or re-paste content you already retrieved wastes "
    "their time and erodes trust. If results genuinely look thin, the fix is "
    "MORE knowledge_search calls with different queries, never a claim that "
    "the file is unavailable. "
    "Non-text, non-image attachments with no filesystem path listed above "
    "are NOT otherwise readable — never guess or invent a path (e.g. "
    "'/mnt/data/...') and call code_interpreter on it. The same applies to a "
    "URL: never guess where the document 'probably' lives on the public "
    "internet (a company's newsroom/investor-relations page, a predictable "
    "filename pattern, etc.) and fetch it via read_url or code_interpreter's "
    "requests/urllib — an uploaded attachment is already indexed and "
    "reachable through knowledge_search, so a network fetch is redundant at "
    "best and, since the guessed URL might not even be the same document, "
    "actively risks presenting the wrong file's numbers as the user's own. "
    "Never fabricate or guess at a file's contents — answering with invented "
    "details is worse than admitting you can't find something in it. "
    "knowledge_search results are labelled '[n] (filename, p.N)'. When you "
    "state a fact taken from a retrieved passage, put the matching marker "
    "(e.g. '[1]') at the end of that sentence — the UI turns it into a "
    "clickable link back to that exact page, so citing is how the user "
    "verifies you. The numbers are stable across every knowledge_search call in this "
    "conversation: reuse the number a passage was labelled with, never "
    "renumber your own citations, and never write a number you did not see "
    "attached to a result. Do not write your own 'Sources' or 'References' "
    "section — the UI builds one from the real citations automatically; a "
    "list you write yourself would not be clickable and would only "
    "duplicate it. "
    "Default to a table for a request that surveys a document (an overview, "
    "a breakdown by section, 'what's in this doc', a comparison across "
    "items) — one row per item, with a dedicated rightmost 'Source' column "
    "holding just that row's citation marker(s), rather than folding the "
    "marker into a prose cell. This reads far better than inline citations "
    "scattered through paragraph text once there's more than a couple of "
    "them. Keep prose-with-inline-'[n]'-at-point-of-claim for an answer "
    "that is genuinely narrative (explaining, reasoning, a single specific "
    "question) — don't force a table onto content that isn't naturally "
    "row-shaped. "
    "If you need code_interpreter to work with an attached PDF directly "
    "(e.g. charting its numbers), read the '{filename}.extracted.md' sibling "
    "file in your working directory instead of re-parsing the PDF's raw "
    "bytes with pypdf/pdfplumber — it's already OCR'd, page-marked, and "
    "includes captions raw extraction would miss."
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


# ── Pure instruction blocks ──────────────────────────────────────────────
#
# Each returns "" when its condition doesn't apply, or the text to append
# when it does. Unlike the mail/calendar functions above, these never touch
# `tools`/`tool_choice` — chat.py appends them in one ordered loop rather
# than a hand-written if-block per one, so the assembly order (which
# matters — see docs/claude_docs/architecture/prompt-and-skills.md) is
# visible in one place, and adding a new one is one more function + one
# more tuple entry.


def existing_task_board_block(has_existing_board: bool) -> str:
    if not has_existing_board:
        return ""
    return (
        "\n\n---\n**Existing task board:**\n"
        "A Kanban task list already exists for this conversation. "
        "If the user is providing new details, context, or corrections, "
        "call manage_tasks action=create_list again with updated, more specific tasks "
        "that incorporate their new information. "
        "Otherwise continue working through the existing tasks."
    )


def readonly_kb_block(ci_has_workspace_access: bool) -> str:
    """Not attachment-specific (unlike attachments_block above) — this is
    about code_interpreter's filesystem generally, so it's unconditional on
    ci_has_workspace_access alone, not on any particular upload existing.
    See BubblewrapRuntime._bwrap_argv()/sandbox_service.py's per-user
    SandboxTemplate for what actually gets mounted at /workspace/.kb."""
    if not ci_has_workspace_access:
        return ""
    return (
        "\n\n---\n**Read-only knowledge base:**\n"
        "If /workspace/.kb exists, it holds the user's standing knowledge-base "
        "content, made available for reference — do not attempt to write, "
        "modify, or delete anything under it; that path is read-only and any "
        "such attempt will fail. Read from it freely."
    )


def attachments_block(
    attachments: list[dict[str, Any]], *, ci_has_workspace_access: bool
) -> str:
    if not attachments:
        return ""
    attachment_lines = "\n".join(
        f"- {a['name']} ({a['mime']})"
        + (
            f" — readable via code_interpreter at the absolute path: {a['workspace_path']}"
            if ci_has_workspace_access and "workspace_path" in a
            else ""
        )
        for a in attachments
    )
    return (
        "\n\n---\n**Attached files:**\n"
        + attachment_lines
        + "\n\n**Attachment handling instructions:**\n"
        + ATTACHMENT_ANALYSIS_INSTRUCTIONS
    )


def custom_instructions_block(raw: str | None) -> str:
    if not raw or not raw.strip():
        return ""
    return "\n\n---\n**Additional instructions from user:**\n" + raw.strip()


__all__ = [
    "ATTACHMENT_ANALYSIS_INSTRUCTIONS",
    "_tool_name",
    "_should_allow_task_planning",
    "_should_force_task_planning",
    "_should_route_workspace_mail_request",
    "_configure_workspace_mail_request",
    "_should_route_calendar_write_request",
    "_configure_calendar_write_request",
    "existing_task_board_block",
    "readonly_kb_block",
    "attachments_block",
    "custom_instructions_block",
]
