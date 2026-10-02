"""``convert.py`` — LibreOffice for the legacy binary formats only; everything modern is read natively and never gets here."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from document_intelligence import convert

DOCUMENTS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "documents"


def test_legacy_is_decided_from_the_bytes():
    assert convert.is_legacy(b"\xd0\xcf\x11\xe0" + bytes(100), "x.bin", None)
    assert convert.is_legacy(b"{\\rtf1\\ansi hello}", "x.txt", "text/plain")
    assert not convert.is_legacy(
        (DOCUMENTS / "sample.docx").read_bytes(), "renamed.doc", "application/msword"
    )  # a docx named .doc is a docx
    assert not convert.is_legacy(
        (DOCUMENTS / "sample.xlsx").read_bytes(), "s.xlsx", None
    )
    assert not convert.is_legacy(b"plain text", "a.txt", "text/plain")


async def test_convert_returns_none_when_the_binary_is_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    async def _no_such_program(*args, **kwargs):
        raise FileNotFoundError("soffice")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_such_program)
    assert await convert.convert_via_libreoffice(b"fake", "doc.doc") is None


async def test_convert_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch):
    class _Hangs:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(60)

        def terminate(self):
            pass

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def _spawn(*args, **kwargs):
        return _Hangs()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    assert (
        await convert.convert_via_libreoffice(b"fake", "doc.doc", timeout_s=0.1) is None
    )
