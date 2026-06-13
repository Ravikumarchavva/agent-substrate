"""InMemoryInbox — Stage 0 in-process implementation of Inbox."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING, Callable

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message
from ravi.kernel.runtime.inbox import DeadLetterEntry, DeadLetterReason

if TYPE_CHECKING:
    pass


class InMemoryInbox:
    """Single-process in-memory Inbox.

    Robustness guarantees honoured (identical to the Protocol contract):
    1. Dedup by Message.id — deliver is idempotent.
    2. Per-sender FIFO — sender key is ``Message.sender`` (str of AgentId) or
       ``"__anon__"`` for anonymous senders.
    3. Retry + dead-letter after ``max_retries`` nacks.

    The ``on_deliver`` callback is called after a new (non-duplicate) message
    is stored — the Scheduler uses this hook to enqueue a wakeup for the agent.
    """

    def __init__(
        self,
        max_retries: int = 3,
        on_deliver: Callable[[AgentId], None] | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._on_deliver = on_deliver

        # msg_id → Message, per agent
        self._messages: dict[AgentId, dict[str, Message]] = defaultdict(dict)
        # per-sender FIFO order: agent_id → sender_key → deque[msg_id]
        self._order: dict[AgentId, dict[str, deque[str]]] = defaultdict(
            lambda: defaultdict(deque)
        )
        # retry counter: (agent_id, msg_id) → attempts
        self._retries: dict[tuple[AgentId, str], int] = defaultdict(int)
        # dead letters
        self._dead: dict[AgentId, list[DeadLetterEntry]] = defaultdict(list)

    def _sender_key(self, msg: Message) -> str:
        return str(msg.sender) if msg.sender else "__anon__"

    async def deliver(self, agent_id: AgentId, msg: Message) -> bool:
        if msg.id in self._messages[agent_id]:
            return False  # duplicate
        self._messages[agent_id][msg.id] = msg
        self._order[agent_id][self._sender_key(msg)].append(msg.id)
        if self._on_deliver:
            self._on_deliver(agent_id)
        return True

    async def drain(self, agent_id: AgentId, *, max: int = 100) -> list[Message]:
        result: list[Message] = []
        msgs = self._messages[agent_id]
        for sender_queue in self._order[agent_id].values():
            for msg_id in list(sender_queue):
                if len(result) >= max:
                    break
                if msg_id in msgs:
                    result.append(msgs[msg_id])
        return result

    async def ack(self, agent_id: AgentId, msg_id: str) -> None:
        self._messages[agent_id].pop(msg_id, None)
        self._retries.pop((agent_id, msg_id), None)
        for q in self._order[agent_id].values():
            try:
                q.remove(msg_id)
            except ValueError:
                pass

    async def nack(self, agent_id: AgentId, msg_id: str, *, error: str = "") -> None:
        key = (agent_id, msg_id)
        self._retries[key] += 1
        if self._retries[key] >= self._max_retries:
            msg = self._messages[agent_id].get(msg_id)
            if msg:
                self._dead[agent_id].append(
                    DeadLetterEntry(
                        agent_id=agent_id,
                        msg=msg,
                        reason=DeadLetterReason.MAX_RETRIES,
                        attempts=self._retries[key],
                        last_error=error or None,
                    )
                )
            await self.ack(agent_id, msg_id)

    async def dead_letters(self, agent_id: AgentId) -> list[DeadLetterEntry]:
        return list(self._dead[agent_id])

    async def pending_count(self, agent_id: AgentId) -> int:
        return len(self._messages[agent_id])
