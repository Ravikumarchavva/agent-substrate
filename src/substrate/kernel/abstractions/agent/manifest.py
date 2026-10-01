"""AgentManifest — minimal self-description type for agent discovery/interop.

Multi-agent supervision (spawn/join/cancel, ``Supervision``, ``Actor``,
``Topic``) is already a first-class kernel concern with no external interop
surface yet. This is a plain declarative value type, not a Protocol — no
implementer obligation, and no repeat of the single-implementer-Protocol
pattern this kernel already got trimmed for once (``AgentContextProtocol``,
``CompactionCoordinator``). Not wired into the ``Agent`` Protocol as a
required field: it exists as a shape for future agent discovery/interop
(mirroring how ``Skill``/MCP tool interop already work), to be there when
that becomes a real requirement, without forcing every current ``Agent``
implementer to produce one today. No consumer builds a registry against
this yet.
"""

from __future__ import annotations

from pydantic import Field

from substrate.kernel.core.content import KernelModel
from substrate.kernel.core.identity import Actor
from substrate.kernel.llm.llm import Modality


class AgentManifest(KernelModel):
    """What an agent is and can do, for discovery/interop — not for running it."""

    agent_id: Actor
    display_name: str
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    modalities: list[Modality] = Field(default_factory=list)
    memory_namespaces: list[str] = Field(default_factory=list)
    version: str = "1"


__all__ = ["AgentManifest"]
