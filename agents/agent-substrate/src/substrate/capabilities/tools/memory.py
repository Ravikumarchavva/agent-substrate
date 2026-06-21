"""MemoryTool — read/write agent memories across conversations.

Exposes both short-term session state (ShortTermMemory) and long-term
persistent facts (LongTermMemory) as a single LLM-callable tool.

Short-term operations (get/set/clear) are scoped to the current session.
Long-term operations (remember/recall/forget) persist across sessions and
are scoped to the agent.
"""

from __future__ import annotations


from substrate.kernel.core.identity import AgentId
from substrate.kernel.storage.memory import LongTermMemory, ShortTermMemory
from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel import TextBlock
from substrate.logger import setup_logging

logger = setup_logging()


class MemoryTool:
    """Read/write agent memories via ShortTermMemory and LongTermMemory protocols."""

    name = "memory"
    description = (
        "Manage agent memory. "
        "Short-term: get/set/clear key-value state within the current session. "
        "Long-term: remember/recall/forget facts that persist across sessions."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "clear_session", "remember", "recall", "forget"],
                "description": (
                    "get — read a session key; "
                    "set — write a session key; "
                    "clear_session — wipe all session state; "
                    "remember — store a long-term fact; "
                    "recall — search long-term facts; "
                    "forget — delete a long-term fact by id."
                ),
            },
            "key": {
                "type": "string",
                "description": "Session key (required for get/set).",
            },
            "value": {
                "type": "string",
                "description": "Value to store (required for set/remember).",
            },
            "query": {
                "type": "string",
                "description": "Search query (required for recall).",
            },
            "memory_id": {
                "type": "string",
                "description": "Memory ID returned by remember (required for forget).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        agent_id: AgentId,
        session_id: str,
        *,
        short_term: ShortTermMemory | None = None,
        long_term: LongTermMemory | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._session_id = session_id
        self._short_term = short_term
        self._long_term = long_term

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        key: str = "",
        value: str = "",
        query: str = "",
        memory_id: str = "",
        **_: object,
    ) -> ToolExecutionResult:
        # ── Short-term operations ─────────────────────────────────────────────
        if action in ("get", "set", "clear_session"):
            if self._short_term is None:
                return ToolExecutionResult(
                    content=[TextBlock(text="Short-term memory is not configured.")],
                    is_error=True,
                )
            return await self._short_term_op(action, key=key, value=value)

        # ── Long-term operations ──────────────────────────────────────────────
        if action in ("remember", "recall", "forget"):
            if self._long_term is None:
                return ToolExecutionResult(
                    content=[TextBlock(text="Long-term memory is not configured.")],
                    is_error=True,
                )
            return await self._long_term_op(
                action, value=value, query=query, memory_id=memory_id
            )

        return ToolExecutionResult(
            content=[TextBlock(text=f"Unknown action: {action!r}")],
            is_error=True,
        )

    async def _short_term_op(
        self, action: str, *, key: str, value: str
    ) -> ToolExecutionResult:
        assert self._short_term is not None
        if action == "get":
            if not key.strip():
                return ToolExecutionResult(
                    content=[TextBlock(text="'key' is required for get.")],
                    is_error=True,
                )
            state = await self._short_term.get_state(self._session_id)
            if key not in state:
                return ToolExecutionResult(
                    content=[TextBlock(text=f"No value for key '{key}'.")]
                )
            return ToolExecutionResult(content=[TextBlock(text=f"{key}: {state[key]}")])

        if action == "set":
            if not key.strip() or not value.strip():
                return ToolExecutionResult(
                    content=[
                        TextBlock(text="Both 'key' and 'value' are required for set.")
                    ],
                    is_error=True,
                )
            await self._short_term.update_state(self._session_id, {key: value})
            return ToolExecutionResult(content=[TextBlock(text=f"Stored '{key}'.")])

        # clear_session
        await self._short_term.clear(self._session_id)
        return ToolExecutionResult(content=[TextBlock(text="Session state cleared.")])

    async def _long_term_op(
        self, action: str, *, value: str, query: str, memory_id: str
    ) -> ToolExecutionResult:
        assert self._long_term is not None
        if action == "remember":
            if not value.strip():
                return ToolExecutionResult(
                    content=[TextBlock(text="'value' is required for remember.")],
                    is_error=True,
                )
            mem_id = await self._long_term.save(self._agent_id, value)
            return ToolExecutionResult(
                content=[TextBlock(text=f"Stored memory (id={mem_id}).")],
                app_data={"memory_id": mem_id},
            )

        if action == "recall":
            if not query.strip():
                return ToolExecutionResult(
                    content=[TextBlock(text="'query' is required for recall.")],
                    is_error=True,
                )
            memories = await self._long_term.search(self._agent_id, query)
            if not memories:
                return ToolExecutionResult(
                    content=[TextBlock(text="No relevant memories found.")]
                )
            lines = [f"[{m.id[:8]}] {m.content}" for m in memories]
            return ToolExecutionResult(content=[TextBlock(text="\n".join(lines))])

        # forget
        if not memory_id.strip():
            return ToolExecutionResult(
                content=[TextBlock(text="'memory_id' is required for forget.")],
                is_error=True,
            )
        deleted = await self._long_term.delete(self._agent_id, memory_id)
        if deleted:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Deleted memory {memory_id}.")]
            )
        return ToolExecutionResult(
            content=[TextBlock(text=f"Memory {memory_id} not found.")],
            is_error=True,
        )
