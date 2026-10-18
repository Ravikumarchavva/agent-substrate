"""The kernel's storage ports: what the engine needs from whoever keeps its state.

    threads    ThreadStore (the conversation DAG), MessageNode, Branch, HistoryCheckpoint
    tasks      TaskStore (an agent's Kanban boards)
    graph      GraphStore (entities and relationships)
    workspace  WorkspaceStore (snapshot trees of a conversation's files)
    files      FileStore (keyed bytes)  ·  blob: BlobStore (content-addressed bytes)

``loamz.agents.stores`` implements the agent's own (threads, tasks, graph, workspace, session state) in SQL;
file bytes, documents and long-term memory are Loamz Data's (``loamz.data.storage``).
"""

from loamz.agents.kernel.ports.blob import BlobStore
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.kernel.ports.graph import (
    CypherCapable,
    Entity,
    EntityFinder,
    GraphStore,
    Relationship,
    SubGraph,
)
from loamz.agents.kernel.ports.tasks import Task, TaskList, TaskStatus, TaskStore
from loamz.agents.kernel.ports.threads import Branch, HistoryCheckpoint, MessageNode, ThreadStore
from loamz.agents.kernel.ports.workspace import (
    ContentRef,
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
    WorkspaceStore,
)

__all__ = [
    "BlobStore",
    "Branch",
    "ContentRef",
    "CypherCapable",
    "Entity",
    "EntityFinder",
    "FileStore",
    "GraphStore",
    "HistoryCheckpoint",
    "MessageNode",
    "Relationship",
    "SubGraph",
    "Task",
    "TaskList",
    "TaskStatus",
    "TaskStore",
    "ThreadStore",
    "WorkspaceFileEntry",
    "WorkspaceManifest",
    "WorkspaceSnapshot",
    "WorkspaceStore",
]
