"""A conversation as something a person can keep: Markdown, or the plain list of messages.

Built from the wire events ``project_thread`` yields. Only what was said is exported: the user's messages and the assistant's answers.
Tool calls, reasoning and file contents are left out (attachments are named, not included), which is also what makes the same function safe
for a public share link.
"""

from __future__ import annotations

from typing import Any


def messages_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``[{"role": "user"|"assistant", "text": ..., "attachments": [names]}]`` in order, assistant text deltas joined into one answer."""
    out: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("type")
        if kind == "user.message":
            out.append(
                {
                    "role": "user",
                    "text": str(event.get("text") or ""),
                    "attachments": [
                        str(a.get("name"))
                        for a in event.get("attachments") or []
                        if isinstance(a, dict) and a.get("name")
                    ],
                }
            )
        elif kind == "text.delta":
            if not out or out[-1]["role"] != "assistant":
                out.append({"role": "assistant", "text": "", "attachments": []})
            out[-1]["text"] += str(event.get("text") or "")
    return [m for m in out if m["text"].strip() or m["attachments"]]


def to_markdown(title: str, messages: list[dict[str, Any]]) -> str:
    lines = [f"# {title.strip() or 'Conversation'}", ""]
    for m in messages:
        lines.append("## You" if m["role"] == "user" else "## Assistant")
        lines.append("")
        if m["attachments"]:
            lines.append("*Attached: " + ", ".join(m["attachments"]) + "*")
            lines.append("")
        if m["text"].strip():
            lines += [m["text"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def filename(title: str, extension: str) -> str:
    """A safe download name for ``title``."""
    slug = "".join(c if c.isalnum() else "-" for c in title.strip().lower()).strip("-")
    return (
        f"{'-'.join(filter(None, slug.split('-')))[:60] or 'conversation'}.{extension}"
    )


__all__ = ["filename", "messages_of", "to_markdown"]
