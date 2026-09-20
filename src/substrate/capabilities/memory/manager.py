"""MemoryManager — extraction, candidate lifecycle, promotion, and contradiction reconciliation.

Higher-layer service (L2) orchestrating the lifecycle of memories across
sessions and speculative execution branches:
- Extraction: produces `CANDIDATE` memories from turns or tool executions.
- Promotion: elevates verified candidates to `ACTIVE` status.
- Reconciliation: marks outdated facts as `SUPERSEDED` and maintains lineage.
- Branch Pruning: discards speculative candidates when an execution branch is abandoned.
"""

from __future__ import annotations

import re
from typing import Sequence

from substrate.kernel.core.content import ChatMessage, Role
from substrate.kernel.storage.memory import (
    MemoryCategory,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
)


_DIRECTIVE_PATTERNS = [
    re.compile(r"\b(?:always|never|prefer|please ensure|remember to)\b", re.IGNORECASE),
    re.compile(r"\b(?:my favorite|i like|i want you to)\b", re.IGNORECASE),
]


class MemoryManager:
    """Orchestrator for cognitive memory lifecycle."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def extract_candidates(
        self,
        messages: Sequence[ChatMessage],
        *,
        namespace: MemoryNamespace,
        provenance: MemoryProvenance | None = None,
    ) -> list[MemoryRecord]:
        """Extract candidate facts and preferences from conversation messages."""
        candidates: list[MemoryRecord] = []
        prov = provenance or MemoryProvenance(extraction_method="rule_heuristic")

        for msg in messages:
            if msg.role not in (Role.USER, Role.ASSISTANT):
                continue
            text = msg.text.strip()
            if not text:
                continue

            # Check if text looks like a standing directive/preference
            is_directive = any(pattern.search(text) for pattern in _DIRECTIVE_PATTERNS)
            category = MemoryCategory.DIRECTIVE if is_directive else MemoryCategory.SEMANTIC

            candidate = MemoryRecord.candidate(
                text,
                category=category,
                namespace=namespace,
                provenance=prov,
            )
            await self._store.save(candidate)
            candidates.append(candidate)

        return candidates

    async def promote(self, candidate_id: str) -> MemoryRecord | None:
        """Promote a CANDIDATE record to ACTIVE canonical status."""
        record = await self._store.get(candidate_id)
        if record is None:
            return None

        # Re-create as ACTIVE
        active_record = MemoryRecord(
            id=record.id,
            content=record.content,
            category=record.category,
            status=MemoryStatus.ACTIVE,
            namespace=record.namespace,
            provenance=record.provenance,
            validity=record.validity,
            lifecycle=record.lifecycle,
            metadata=record.metadata,
        )
        await self._store.save(active_record)
        return active_record

    async def reject(self, candidate_id: str) -> bool:
        """Reject and delete a candidate record."""
        return await self._store.delete(candidate_id)

    async def reconcile_and_save(
        self,
        new_record: MemoryRecord,
        *,
        supersedes_id: str | None = None,
    ) -> str:
        """Save a new memory record and mark any superseded prior record as SUPERSEDED."""
        if supersedes_id:
            old_record = await self._store.get(supersedes_id)
            if old_record is not None:
                superseded = MemoryRecord(
                    id=old_record.id,
                    content=old_record.content,
                    category=old_record.category,
                    status=MemoryStatus.SUPERSEDED,
                    namespace=old_record.namespace,
                    provenance=old_record.provenance,
                    validity=old_record.validity,
                    lifecycle=old_record.lifecycle,
                    metadata=old_record.metadata,
                )
                await self._store.save(superseded)

            # Ensure new_record reflects supersedes_id lineage
            if new_record.provenance.supersedes_id != supersedes_id:
                updated_provenance = MemoryProvenance(
                    source_session_id=new_record.provenance.source_session_id,
                    source_node_id=new_record.provenance.source_node_id,
                    source_branch_id=new_record.provenance.source_branch_id,
                    source_run_id=new_record.provenance.source_run_id,
                    confidence=new_record.provenance.confidence,
                    extraction_method=new_record.provenance.extraction_method,
                    supersedes_id=supersedes_id,
                )
                new_record = MemoryRecord(
                    id=new_record.id,
                    content=new_record.content,
                    category=new_record.category,
                    status=new_record.status,
                    namespace=new_record.namespace,
                    provenance=updated_provenance,
                    validity=new_record.validity,
                    lifecycle=new_record.lifecycle,
                    metadata=new_record.metadata,
                )

        return await self._store.save(new_record)

    async def discard_branch(self, branch_id: str, namespace: MemoryNamespace) -> int:
        """Purge all speculative CANDIDATE memories belonging to an abandoned branch."""
        # Query candidate records in namespace
        matches = await self._store.query(
            MemoryQuery(
                namespace=namespace,
                statuses=(MemoryStatus.CANDIDATE,),
                limit=500,
            )
        )
        discarded_count = 0
        for match in matches:
            if match.record.provenance.source_branch_id == branch_id:
                deleted = await self._store.delete(match.record.id)
                if deleted:
                    discarded_count += 1

        return discarded_count


__all__ = ["MemoryManager"]

