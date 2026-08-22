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

logger = setup_logging("substrate.doc_handler.security_scan")

# doc-firewall's own verdict -> our severity. FLAG is a real, structural
# finding worth blocking on (not a soft "maybe") — same "any hit is a hit"
# posture the rest of this pipeline takes for prompt-attack detection.
_VERDICT_SEVERITY = {
    "ALLOW": Severity.NONE,
    "FLAG": Severity.HIGH,
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
