"""Subagent progress tree — live hierarchy of orchestrator + worker agents.

Rebuilds the agent tree from a single stream of :class:`AgentProgress` events
using their ``agent_id`` / ``parent_id`` / ``depth`` / ``seq`` fields, and renders
it as a Rich ``Tree`` with per-agent status and a spinner while running.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text
from rich.tree import Tree

from substrate.kernel.messaging.stream import AgentProgress, AgentStep

from .theme import ConsoleTheme

_RUNNING_STEPS = {AgentStep.STARTED, AgentStep.THINKING}


@dataclass
class _Node:
    key: str
    parent_key: str | None
    depth: int
    step: AgentStep = AgentStep.STARTED
    detail: str = ""
    seq: int = 0
    order: int = 0  # first-seen ordering for stable display


@dataclass
class SubagentTracker:
    """Accumulates progress events into a renderable agent hierarchy."""

    nodes: dict[str, _Node] = field(default_factory=dict)
    _counter: int = 0

    def ingest(self, ev: AgentProgress) -> None:
        key = ev.agent_id.key
        parent_key = ev.parent_id.key if ev.parent_id is not None else None
        node = self.nodes.get(key)
        if node is None:
            node = _Node(
                key=key, parent_key=parent_key, depth=ev.depth, order=self._counter
            )
            self._counter += 1
            self.nodes[key] = node
        # Only advance on newer events (seq is strictly increasing per run).
        if ev.seq >= node.seq:
            node.seq = ev.seq
            node.step = ev.step
            node.detail = ev.content
            if parent_key is not None:
                node.parent_key = parent_key

        # Ensure a referenced parent exists so children can nest under it.
        if parent_key is not None and parent_key not in self.nodes:
            self.nodes[parent_key] = _Node(
                key=parent_key, parent_key=None, depth=0, order=self._counter
            )
            self._counter += 1

    @property
    def has_subagents(self) -> bool:
        """True once any agent below the root (depth > 0) has reported."""
        return any(n.depth > 0 for n in self.nodes.values())

    def _label(self, node: _Node, theme: ConsoleTheme) -> Text:
        if node.step == AgentStep.DONE:
            icon, style = theme.ok_icon, "tool_ok"
        elif node.step == AgentStep.ERROR:
            icon, style = theme.err_icon, "tool_err"
        elif node.step == AgentStep.PAUSED:
            icon, style = "⏸", "info"
        elif node.step in _RUNNING_STEPS or node.step == AgentStep.TOOL_CALL:
            icon, style = "▸", "subagent"
        else:
            icon, style = "•", "subagent"

        detail = {
            AgentStep.THINKING: "thinking…",
            AgentStep.TOOL_CALL: f"tool: {node.detail}",
            AgentStep.HANDOFF: f"handoff: {node.detail}",
            AgentStep.DONE: "done",
            AgentStep.ERROR: f"error: {node.detail}",
        }.get(node.step, node.detail or "")

        text = Text()
        text.append(f"{icon} ", style=style)
        text.append(node.key, style="subagent")
        if detail:
            text.append(f"  {detail}", style="info")
        return text

    def render(self, theme: ConsoleTheme) -> Tree | None:
        """Render the hierarchy, or ``None`` for a flat single-agent run."""
        if not self.has_subagents:
            return None

        ordered = sorted(self.nodes.values(), key=lambda n: (n.depth, n.order))
        roots = [n for n in ordered if n.depth == 0 or n.parent_key is None]
        if not roots:
            roots = [n for n in ordered if n.depth == min(x.depth for x in ordered)]

        tree = Tree(Text("agents", style="dim"))
        rich_nodes: dict[str, Tree] = {}

        def attach(node: _Node, parent_tree: Tree) -> None:
            branch = parent_tree.add(self._label(node, theme))
            rich_nodes[node.key] = branch
            for child in ordered:
                if child.parent_key == node.key and child.key != node.key:
                    attach(child, branch)

        for root in roots:
            attach(root, tree)
        return tree
