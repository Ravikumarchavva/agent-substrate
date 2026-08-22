"""Document structural/security scanner — thin wrapper over `doc-firewall`.

Runs on raw uploaded bytes, before PaddleOCR (or any parser) touches them —
a hostile file must not reach the parser first. Verified directly against
this deployment (not assumed from the package's own docs): a clean PDF
fixture scores `ALLOW`/0.0 risk in ~11ms; a synthetically constructed PDF
carrying an OpenAction JavaScript annotation scores `FLAG`/0.876 with four
concrete findings (`/JavaScript` and `/JS` tokens, JS-in-annotation-action,
active-content indicator) — real detection, not a stub.

Default install is pure regex/heuristic/format-specific checks — no ML
deps, matching this codebase's established no-torch-unless-necessary
philosophy (see `text_classifier.py`'s docstring for the parallel
reasoning). `doc-firewall`'s own optional `ml` extra (BERT/embeddings-based
detection, pulls torch+transformers) is deliberately NOT installed.
"""

from __future__ import annotations

from substrate.kernel.agent.safety import SafetyVerdict, Severity
from substrate.logger import setup_logging

logger = setup_logging("substrate.runtimes.document_intelligence.security_scan")

# doc-firewall's own top-level verdict alone is NOT a safe severity signal
# for FLAG: verdict_class (REVIEW vs BLOCK) doesn't discriminate either — a
# confirmed-malicious PDF (real /JavaScript OpenAction) and a benign one
# (unconfirmed byte-level object/filter density counts) both land on
# verdict=FLAG with EVERY finding at verdict_class=REVIEW, because this
# install's BLOCK-tier detectors (YARA/EICAR) aren't active (see
# "reduced-coverage mode" in scan_document's own scan result). An
# evidence-shape check doesn't work either — a T6_DOS "Circular XObject"
# finding on a *benign* PDF still carries a descriptive evidence string,
# same shape as a real detection.
#
# Real, found-not-assumed: ran scan_bytes directly against (a) 5 benign
# Wikipedia-derived benchmark PDFs that were all getting hard-rejected —
# every one of their findings was threat_id T6_DOS or T3_OBFUSCATION, from
# `fast_scan.pdf.dos`/`fast_scan.pdf.obfuscation` — heuristic byte-level
# object/filter-density counts about whether the file is risky to *parse*,
# not about it containing something designed to *execute* — and (b) the
# test suite's synthetic OpenAction-JavaScript PDF, whose findings are all
# threat_id T2_ACTIVE_CONTENT. That threat-category split is the real,
# stable signal: T6_DOS/T3_OBFUSCATION-only reports get downgraded; any
# other category (active content, malware, prompt injection, embedded
# payload, ...) keeps full severity.
_STRUCTURAL_ONLY_THREATS = {"T6_DOS", "T3_OBFUSCATION"}

_VERDICT_SEVERITY = {
    "ALLOW": Severity.NONE,
    "BLOCK": Severity.CRITICAL,
}


def scan_document(data: bytes, *, filename: str = "") -> SafetyVerdict:
    """Sync, CPU-bound (regex/structural parsing, not ML by default) —
    callers run this via ``asyncio.to_thread`` same as the ONNX classifiers.

    Fails open on a scan error (malformed/unsupported file that
    ``doc-firewall`` itself can't parse) — an unscannable file is not the
    same claim as a malicious one; the extraction pipeline's own parser
    will separately reject anything it can't handle.
    """
    from doc_firewall import scan_bytes

    try:
        report = scan_bytes(data, filename=filename or None)
    except Exception as exc:
        logger.warning("doc-firewall scan failed for %r: %s", filename, exc)
        return SafetyVerdict(
            severity=Severity.NONE,
            detector="doc_firewall",
            modality="document",
            detail=f"scan error (fail-open): {exc}",
        )

    verdict_name = (
        report.verdict.name if hasattr(report.verdict, "name") else str(report.verdict)
    )
    if verdict_name == "FLAG":
        # MEDIUM only if every finding is a structural/DoS-class heuristic
        # (T6_DOS/T3_OBFUSCATION) — any other threat category keeps HIGH.
        # See the _STRUCTURAL_ONLY_THREATS comment above for the real PDFs
        # this was verified against.
        threat_ids = {
            f.threat_id.value if hasattr(f.threat_id, "value") else str(f.threat_id)
            for f in report.findings
        }
        structural_only = bool(threat_ids) and threat_ids <= _STRUCTURAL_ONLY_THREATS
        severity = Severity.MEDIUM if structural_only else Severity.HIGH
    else:
        severity = _VERDICT_SEVERITY.get(verdict_name, Severity.NONE)

    findings_summary = [
        f"{f.threat_id.value if hasattr(f.threat_id, 'value') else f.threat_id}: {f.title}"
        for f in report.findings
    ]

    return SafetyVerdict(
        severity=severity,
        scores={"risk_score": float(report.risk_score)},
        detector="doc_firewall",
        modality="document",
        detail="; ".join(findings_summary) if findings_summary else "",
    )


__all__ = ["scan_document"]
