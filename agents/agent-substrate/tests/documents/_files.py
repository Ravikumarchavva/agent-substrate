"""Shared by the reader tests: committed fixtures, and hostile files built on the spot."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DOCUMENTS = FIXTURES / "documents"


def fixture(name: str) -> bytes:
    path = DOCUMENTS / name if (DOCUMENTS / name).exists() else FIXTURES / name
    return path.read_bytes()


def zip_of(members: dict[str, bytes], *, flag_bits: int = 0) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            info = zipfile.ZipInfo(name)
            info.flag_bits = flag_bits
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    return buffer.getvalue()


_MINIMAL_DOCX = {
    "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
}


def docx_with(document_xml: bytes, **extra: bytes) -> bytes:
    return zip_of({**_MINIMAL_DOCX, "word/document.xml": document_xml, **extra})


W = b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def paragraph_xml(text: str) -> bytes:
    return b"<w:p><w:r><w:t>" + text.encode() + b"</w:t></w:r></w:p>"


def zip_bomb_docx() -> bytes:
    """A DOCX whose one huge part inflates ~1000:1 — a few hundred KB that wants hundreds of MB."""
    return docx_with(b"<w:document " + W + b"><w:body>" + paragraph_xml("hi") + b"</w:body></w:document>", **{"word/bomb.bin": b"\x00" * (300 * 1024 * 1024)})


def entity_bomb_docx() -> bytes:
    lol = b'<!DOCTYPE lolz [<!ENTITY a "AAAAAAAAAA"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    return docx_with(lol + b"<w:document " + W + b"><w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p></w:body></w:document>")


def external_entity_docx() -> bytes:
    xxe = b'<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    return docx_with(xxe + b"<w:document " + W + b"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>")


def deeply_nested_docx(depth: int = 5000) -> bytes:
    inner = b"<w:p>" * depth + b"<w:r><w:t>x</w:t></w:r>" + b"</w:p>" * depth
    return docx_with(b"<w:document " + W + b"><w:body>" + inner + b"</w:body></w:document>")


def many_members_docx(count: int = 6000) -> bytes:
    return zip_of({**_MINIMAL_DOCX, "word/document.xml": b"<w:document " + W + b"/>", **{f"x/{i}.txt": b"a" for i in range(count)}})


def encrypted_flag_docx() -> bytes:
    return docx_with(b"<w:document " + W + b"><w:body/></w:document>", **{"word/secret.bin": b"x"})  # flag set below


def with_encryption_flag(data: bytes) -> bytes:
    """``data`` (a zip) with the 'encrypted' bit set on every member's central-directory entry."""
    out = bytearray(data)
    index = 0
    while (index := out.find(b"PK\x01\x02", index)) != -1:
        out[index + 8] |= 0x01  # general-purpose bit flag, low byte
        index += 4
    return bytes(out)
