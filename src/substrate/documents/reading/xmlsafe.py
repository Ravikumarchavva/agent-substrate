"""Reading untrusted XML and zip containers — Office and OpenDocument files are both — without trusting them.

* XML is parsed with expat directly and **refuses** any DTD, entity declaration or external entity (so no entity-expansion bomb, no
  external file or network read), nests no deeper than 200 levels, and holds at most ``MAX_NODES`` elements.
* A zip is opened only if it has a sane number of members, a bounded total size, no member compressed more than 200:1, and no
  encryption; every member is read through a hard byte cap, because a zip header can lie about the size it will inflate to.
* An entry that is not a plain relative path is simply never looked up.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from xml.parsers import expat

MAX_MEMBERS = 5000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_RATIO = 200
MAX_PART_BYTES = 96 * 1024 * 1024
MAX_MEDIA_BYTES = 12 * 1024 * 1024
MAX_DEPTH = 200
MAX_NODES = 4_000_000


class UnsafeDocument(ValueError):
    """The file is malformed, hostile, or uses a feature this reader refuses (encryption, DTDs)."""


def parse_xml(data: bytes) -> ET.Element:
    """The root element of ``data``, as an ``ElementTree`` element. Raises ``UnsafeDocument``."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[ET.Element] = []
    state: dict[str, object] = {"root": None, "nodes": 0, "last": None}

    def clark(name: str) -> str:
        return "{" + name if "}" in name else name

    def refuse(*_args: object) -> None:
        raise UnsafeDocument("document type and entity declarations are not allowed")

    def start(name: str, attrs: dict[str, str]) -> None:
        state["nodes"] = int(state["nodes"]) + 1  # type: ignore[call-overload]
        if int(state["nodes"]) > MAX_NODES or len(stack) >= MAX_DEPTH:  # type: ignore[call-overload]
            raise UnsafeDocument("document is too large or too deeply nested")
        element = ET.Element(clark(name), {clark(k): v for k, v in attrs.items()})
        if stack:
            stack[-1].append(element)
        else:
            state["root"] = element
        stack.append(element)
        state["last"] = None

    def end(_name: str) -> None:
        state["last"] = stack.pop()

    def chars(text: str) -> None:
        last = state["last"]
        if last is not None:  # text after a child closes is that child's tail
            last.tail = (last.tail or "") + text  # type: ignore[attr-defined]
        elif stack:
            stack[-1].text = (stack[-1].text or "") + text

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise UnsafeDocument(f"malformed XML: {exc}") from exc
    root = state["root"]
    if root is None:
        raise UnsafeDocument("empty XML part")
    return root  # type: ignore[return-value]


class Package:
    """A zip container opened under the guards in the module docstring."""

    def __init__(self, data: bytes) -> None:
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            raise UnsafeDocument(f"not a readable zip container: {exc}") from exc
        infos = self._zip.infolist()
        if len(infos) > MAX_MEMBERS:
            raise UnsafeDocument(f"archive has {len(infos)} members (limit {MAX_MEMBERS})")
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise UnsafeDocument("archive inflates to more than the size limit")
        for info in infos:
            if info.flag_bits & 0x1:
                raise UnsafeDocument("the file is encrypted; this reader does not open encrypted documents")
            if info.compress_size and info.file_size > 1_000_000 and info.file_size / info.compress_size > MAX_RATIO:
                raise UnsafeDocument(f"{info.filename!r} is compressed {info.file_size // info.compress_size}:1 — refusing a zip bomb")
        self._names = {i.filename: i for i in infos}

    @property
    def names(self) -> set[str]:
        return set(self._names)

    def read(self, name: str, *, limit: int = MAX_PART_BYTES) -> bytes | None:
        """The member's bytes (at most ``limit``), or ``None`` if there is no such member. Raises ``UnsafeDocument`` over the limit."""
        info = self._names.get(name)
        if info is None or info.is_dir():
            return None
        if info.file_size > limit:
            raise UnsafeDocument(f"{name!r} is larger than {limit} bytes")
        try:
            with self._zip.open(info) as handle:
                body = handle.read(limit + 1)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as exc:
            raise UnsafeDocument(f"cannot read {name!r}: {exc}") from exc
        if len(body) > limit:
            raise UnsafeDocument(f"{name!r} inflates past {limit} bytes")
        return body

    def xml(self, name: str) -> ET.Element | None:
        body = self.read(name)
        return None if body is None else parse_xml(body)

    def close(self) -> None:
        self._zip.close()


def resolve(part: str, target: str) -> str:
    """The zip path ``target`` names, relative to the part that references it."""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


__all__ = ["MAX_MEDIA_BYTES", "Package", "UnsafeDocument", "parse_xml", "resolve"]
