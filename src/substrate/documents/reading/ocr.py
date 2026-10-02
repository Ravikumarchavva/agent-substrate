"""OCR engines behind the ``Ocr`` port, and the choice between them.

* ``TesseractOcr`` drives the ``tesseract`` program through its command line: no Python package, so a plain install costs nothing
  for it, and 100+ languages (``apt install tesseract-ocr`` plus a language pack).
* ``RapidOcr`` runs PaddleOCR's own small models on ONNX Runtime — pip-only, no system program (the ``ocr`` extra).

``resolve_ocr("auto")`` prefers RapidOCR when it is installed, then Tesseract if the program is on the path, then nothing — in which
case the reader reports pages as needing OCR rather than returning them silently empty.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Sequence

from substrate.documents.protocols import Ocr
from substrate.documents.types import OcrResult

logger = logging.getLogger(__name__)

_LANGS_LOCK = threading.Lock()
_LANGS: dict[str, frozenset[str]] = {}


class TesseractOcr:
    name = "tesseract"

    def __init__(self, *, binary: str | None = None, timeout_s: float = 60.0, psm: int = 3) -> None:
        self.binary = binary or shutil.which("tesseract") or "tesseract"
        self.timeout_s = timeout_s
        self.psm = psm

    @classmethod
    def available(cls, binary: str | None = None) -> bool:
        return bool(binary or shutil.which("tesseract"))

    def languages(self) -> frozenset[str]:
        """The language packs this installation has (``tesseract --list-langs``), looked up once."""
        with _LANGS_LOCK:
            if self.binary not in _LANGS:
                try:
                    out = subprocess.run([self.binary, "--list-langs"], capture_output=True, text=True, timeout=20, check=False)
                    _LANGS[self.binary] = frozenset(line.strip() for line in out.stdout.splitlines()[1:] if line.strip())
                except (OSError, subprocess.SubprocessError):
                    _LANGS[self.binary] = frozenset()
            return _LANGS[self.binary]

    def recognize(self, png: bytes, *, languages: Sequence[str] = ("eng",)) -> OcrResult:
        installed = self.languages()
        wanted = [lang for lang in languages if lang in installed] or (["eng"] if "eng" in installed else [])
        if not wanted:
            return OcrResult(error=f"tesseract has none of the language packs {list(languages)} installed")
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            run = subprocess.run(
                [self.binary, "stdin", "stdout", "-l", "+".join(wanted), "--psm", str(self.psm), "tsv"],
                input=png,
                capture_output=True,
                timeout=self.timeout_s,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return OcrResult(error=f"tesseract timed out after {self.timeout_s:g}s")
        except OSError as exc:
            return OcrResult(error=f"tesseract could not run: {exc}")
        if run.returncode != 0:
            return OcrResult(error=f"tesseract failed: {run.stderr.decode('utf-8', 'replace').strip()[:200]}")
        return _from_tsv(run.stdout.decode("utf-8", "replace"))


def _from_tsv(tsv: str) -> OcrResult:
    """Text (lines in order, blank line between paragraphs) and mean word confidence from Tesseract's TSV output."""
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    order: list[tuple[int, int, int]] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        if not text:
            continue
        try:
            key = (int(cols[2]), int(cols[3]), int(cols[4]))
            confidence = float(cols[10])
        except ValueError:
            continue
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(text)
        if confidence >= 0:
            confidences.append(confidence)
    out: list[str] = []
    previous: tuple[int, int] | None = None
    for key in order:
        paragraph = (key[0], key[1])
        if previous is not None and paragraph != previous:
            out.append("")
        out.append(" ".join(lines[key]))
        previous = paragraph
    return OcrResult(text="\n".join(out), confidence=sum(confidences) / len(confidences) if confidences else 0.0)


class RapidOcr:
    name = "rapidocr"

    def __init__(self) -> None:
        self._engine: object | None = None

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("rapidocr") is not None

    def warm(self) -> None:
        """Load the models now (a worker does this before it caps its address space: onnxruntime maps far more than it uses)."""
        from substrate.documents.reading.png import encode_png  # noqa: PLC0415

        self.recognize(encode_png(32, 32, bytes([255]) * 1024))

    def recognize(self, png: bytes, *, languages: Sequence[str] = ("eng",)) -> OcrResult:
        try:
            if self._engine is None:
                from rapidocr import RapidOCR  # the `ocr` extra; models ship inside the wheel

                self._engine = RapidOCR(params={"Global.log_level": "warning"})
            result = self._engine(png)  # type: ignore[operator]
            texts = list(getattr(result, "txts", None) or [])
            scores = list(getattr(result, "scores", None) or [])
        except Exception as exc:  # noqa: BLE001 — an unreadable image or a broken install is a result, not a crash
            return OcrResult(error=f"rapidocr failed: {exc}")
        return OcrResult(text="\n".join(texts), confidence=(sum(scores) / len(scores) * 100) if scores else 0.0)


def resolve_ocr(spec: str | Ocr | None = "auto") -> Ocr | None:
    """The OCR engine for ``spec``: an ``Ocr`` instance as is; ``"auto"``, ``"tesseract"``, ``"rapidocr"``, or ``None``/``"none"``.

    ``"auto"`` and a named engine that is not installed yield ``None`` (auto) or raise ``RuntimeError`` (named)."""
    if spec is None or spec == "none":
        return None
    if not isinstance(spec, str):
        return spec
    if spec == "auto":
        if RapidOcr.available():
            return RapidOcr()
        return TesseractOcr() if TesseractOcr.available() else None
    if spec == "tesseract":
        if not TesseractOcr.available():
            raise RuntimeError("ocr='tesseract' but no `tesseract` program is installed (apt install tesseract-ocr)")
        return TesseractOcr()
    if spec == "rapidocr":
        if not RapidOcr.available():
            raise RuntimeError("ocr='rapidocr' but it is not installed (pip install 'agent-substrate[ocr]')")
        return RapidOcr()
    raise ValueError(f"unknown OCR engine {spec!r}: use 'auto', 'tesseract', 'rapidocr', or an Ocr instance")


__all__ = ["RapidOcr", "TesseractOcr", "resolve_ocr"]
