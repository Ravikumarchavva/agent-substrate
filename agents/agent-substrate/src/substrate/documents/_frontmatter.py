"""A small, forgiving codec for the YAML that OKF frontmatter uses — standard library only.

Frontmatter is a mapping of keys to scalars, lists and nested mappings. This reads the subset real producers write (block and
flow collections, plain / single- / double-quoted scalars, ``|`` and ``>`` block scalars, comments) and writes a subset that every
YAML parser reads back identically: plain scalars where that is unambiguous, JSON (which is valid YAML) for everything else.

**It never raises on content.** The format's first rule is that a consumer does not reject a file for what it cannot understand, so
a value that fails to parse is kept as its raw text and the rest of the mapping is read as usual. Dates and timestamps stay
strings (YAML resolves them to datetimes; no consumer here wants that).
"""

from __future__ import annotations

import json
import re
from typing import Any

_INT = re.compile(r"^[+-]?(0|[1-9][0-9]*)$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+[eE][+-]?\d+)([eE][+-]?\d+)?$")
_PLAIN_SAFE = re.compile(r"^[A-Za-z][A-Za-z0-9 _./()+@,-]*$")
_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}


# ---------------------------------------------------------------------------------------------------------------- reading


def load_mapping(text: str) -> dict[str, Any]:
    """The top-level mapping in ``text``. Lines that are not ``key: value`` at column 0 are ignored; a value that cannot be
    parsed is kept as its raw text."""
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#") or line[0] in " \t":
            i += 1
            continue
        key, rest = _split_key(line)
        if key is None:
            i += 1
            continue
        start = i
        try:
            value, i = _value(lines, i, 0, rest)
        except Exception:  # noqa: BLE001 — a value we cannot read is kept, not rejected
            end = start + 1
            while end < len(lines) and (not lines[end] or lines[end][0] in " \t"):
                end += 1
            value, i = "\n".join([rest, *lines[start + 1 : end]]).strip(), end
        result[key] = value
    return result


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_key(content: str) -> tuple[str | None, str]:
    """``(key, rest)`` for ``key: rest``, or ``(None, content)`` when ``content`` is not a mapping entry."""
    if content[:1] in ('"', "'"):
        quote = content[0]
        end = 1
        while end < len(content):
            if content[end] == "\\" and quote == '"':
                end += 2
                continue
            if content[end] == quote:
                if quote == "'" and content[end + 1 : end + 2] == "'":
                    end += 2
                    continue
                break
            end += 1
        after = content[end + 1 :]
        if (
            end < len(content)
            and after.startswith(":")
            and (len(after) == 1 or after[1] == " ")
        ):
            return _quoted(content[: end + 1]), after[1:].strip()
        return None, content
    if content[:1] in "[{-#" and not content.startswith("-" * 3):
        if content[:1] in "[{#":
            return None, content
    match = re.match(r"^([^\s:#][^:#]*?)\s*:(?:\s+|$)(.*)$", content)
    if match is None:
        return None, content
    return match.group(1).strip(), match.group(2).strip()


def _strip_comment(rest: str) -> str:
    if rest[:1] in ('"', "'"):
        return rest
    cut = re.search(r"\s#", rest)
    return rest[: cut.start()].rstrip() if cut else rest


def _value(lines: list[str], i: int, indent: int, rest: str) -> tuple[Any, int]:
    """The value whose first text is ``rest`` (the remainder of line ``i``); returns it and the next line to read."""
    rest = _strip_comment(rest) if rest[:1] not in ("|", ">") else rest
    if rest[:1] in ("|", ">"):
        return _block_scalar(
            lines, i + 1, indent, folded=rest[0] == ">", chomp=rest[1:2]
        )
    if rest:
        value, tail = _flow(rest, 0)
        if (
            tail.strip()
        ):  # trailing text after a complete value: it was a plain scalar after all
            return _plain(rest), i + 1
        return value, i + 1
    # no inline value: a nested block follows (deeper), or a sequence at the same indent
    j = i + 1
    while j < len(lines) and (
        not lines[j].strip() or lines[j].lstrip().startswith("#")
    ):
        j += 1
    if j >= len(lines):
        return None, j
    nxt = lines[j]
    if _indent(nxt) > indent or (
        _indent(nxt) == indent and nxt.lstrip().startswith("- ") or nxt.strip() == "-"
    ):
        return _block(lines, j, _indent(nxt))
    return None, i + 1


def _block(lines: list[str], i: int, indent: int) -> tuple[Any, int]:
    if lines[i].lstrip().startswith("- ") or lines[i].strip() == "-":
        return _sequence(lines, i, indent)
    return _mapping(lines, i, indent)


def _mapping(lines: list[str], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if _indent(line) != indent or line.lstrip().startswith("- "):
            break
        key, rest = _split_key(line.strip())
        if key is None:
            i += 1
            continue
        out[key], i = _value(lines, i, indent, rest)
    return out, i


def _sequence(lines: list[str], i: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if _indent(line) != indent or not (
            line.lstrip().startswith("- ") or line.strip() == "-"
        ):
            break
        item = line.lstrip()[1:]
        content = item.strip()
        if not content:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _indent(lines[j]) > indent:
                value, i = _block(lines, j, _indent(lines[j]))
                out.append(value)
            else:
                out.append(None)
                i += 1
            continue
        key, rest = _split_key(content)
        if key is not None and content[:1] not in "[{":
            # "- key: value" begins a mapping whose other keys line up with this one
            column = indent + 1 + (len(item) - len(item.lstrip()))
            lines = lines.copy()
            lines[i] = " " * column + content
            value, i = _mapping(lines, i, column)
            out.append(value)
        else:
            value, _tail = _flow(_strip_comment(content), 0)
            out.append(value)
            i += 1
    return out, i


def _block_scalar(
    lines: list[str], i: int, indent: int, *, folded: bool, chomp: str
) -> tuple[str, int]:
    body: list[str] = []
    width: int | None = None
    while i < len(lines):
        line = lines[i]
        if line.strip():
            here = _indent(line)
            if here <= indent:
                break
            if width is None:
                width = here
            body.append(line[width:] if here >= width else line.lstrip())
        else:
            body.append("")
        i += 1
    while body and body[-1] == "" and chomp != "+":
        body.pop()
    if folded:
        text = ""
        for k, part in enumerate(body):
            if k and part and body[k - 1]:
                text += " "
            elif k and (not part or not body[k - 1]):
                text += "\n"
            text += part
    else:
        text = "\n".join(body)
    return (text + "\n" if chomp == "" and text else text), i


def _quoted(token: str) -> str:
    if token[0] == '"':
        try:
            return json.loads(token)
        except ValueError:
            return re.sub(r"\\(.)", r"\1", token[1:-1])
    return token[1:-1].replace("''", "'")


def _plain(token: str) -> Any:
    token = token.strip()
    if token in ("", "~") or token.lower() == "null":
        return None
    if token.lower() in ("true", "false"):
        return token.lower() == "true"
    if _INT.match(token):
        return int(token)
    if _FLOAT.match(token):
        return float(token)
    return token


def _flow(text: str, i: int, *, in_collection: bool = False) -> tuple[Any, str]:
    """One flow value starting at ``text[i:]``; returns it and the unread remainder."""
    s = text[i:].lstrip()
    if s[:1] == "[":
        items: list[Any] = []
        s = s[1:].lstrip()
        while s and s[0] != "]":
            value, s = _flow(s, 0, in_collection=True)
            items.append(value)
            s = s.lstrip()
            if s[:1] == ",":
                s = s[1:].lstrip()
        return items, s[1:]
    if s[:1] == "{":
        out: dict[str, Any] = {}
        s = s[1:].lstrip()
        while s and s[0] != "}":
            if s[0] in ('"', "'"):
                end = _quote_end(s)
                key, s = _quoted(s[: end + 1]), s[end + 1 :].lstrip()
            else:
                m = re.match(r"^([^:,}]+?)\s*(?=:|,|\})", s)
                key, s = (m.group(1).strip(), s[m.end() :]) if m else (s.strip(), "")
            s = s.lstrip()
            if s[:1] == ":":
                value, s = _flow(s[1:], 0, in_collection=True)
            else:
                value = None
            out[str(key)] = value
            s = s.lstrip()
            if s[:1] == ",":
                s = s[1:].lstrip()
        return out, s[1:]
    if s[:1] in ('"', "'"):
        end = _quote_end(s)
        return _quoted(s[: end + 1]), s[end + 1 :]
    if in_collection:
        m = re.match(r"^([^,\]}]*)", s)
        token = m.group(1) if m else s
        return _plain(token), s[len(token) :]
    return _plain(s), ""


def _quote_end(s: str) -> int:
    quote, end = s[0], 1
    while end < len(s):
        if s[end] == "\\" and quote == '"':
            end += 2
            continue
        if s[end] == quote:
            if quote == "'" and s[end + 1 : end + 2] == "'":
                end += 2
                continue
            return end
        end += 1
    return len(s) - 1


# ---------------------------------------------------------------------------------------------------------------- writing


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        return (
            text.replace("e", ".0e", 1) if ("e" in text and "." not in text) else text
        )
    text = str(value)
    if (
        _PLAIN_SAFE.match(text)
        and text.lower() not in _YAML_WORDS
        and not text.endswith(" ")
    ):
        return text
    return json.dumps(text, ensure_ascii=False)


def _render(value: Any) -> str:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return _scalar(value)


def dump_mapping(data: dict[str, Any]) -> str:
    """``data`` as frontmatter lines, one key per line, in order."""
    lines = []
    for key, value in data.items():
        name = str(key)
        lines.append(
            f"{name if _PLAIN_KEY.match(name) else json.dumps(name, ensure_ascii=False)}: {_render(value)}"
        )
    return "\n".join(lines)


__all__ = ["dump_mapping", "load_mapping"]
