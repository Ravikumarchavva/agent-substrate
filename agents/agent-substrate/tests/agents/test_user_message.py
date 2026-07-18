"""user.message EventLogProtocol entry — the log's self-complete record of the turn
that started a run, which history is projected from (see WS4 persistence
redesign: the EventLogProtocol becomes the single source of truth for conversation
history, replacing the old steps-table write path)."""

from __future__ import annotations

from substrate.agents.core.react import ReActAgent
from substrate.agents.runtime import Runtime
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.usage import Usage
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta


class _StubLLM:
    model = "stub"

    async def generate_stream(self, messages, *, options, ctx=None):
        yield TextDelta(text="answer")
        yield CompletionEvent(content=[TextBlock(text="answer")], usage=Usage())


async def _log_kinds(rt: Runtime, run_id: str) -> list[tuple[str, dict]]:
    return [(e.kind, e.payload) async for e in rt.event_log.read(run_id)]


async def test_user_message_defaults_to_the_turn_text() -> None:
    """No display_text metadata set (e.g. via Runtime.run()) -> falls back to
    the actual message content."""
    agent = ReActAgent("assistant", model=_StubLLM())
    async with Runtime() as rt:
        result = await rt.run(agent, "What is 2+2?")
        entries = await _log_kinds(rt, result.run_id)

    user_messages = [p for k, p in entries if k == "user.message"]
    assert len(user_messages) == 1
    assert user_messages[0] == {"text": "What is 2+2?", "attachments": []}


async def test_user_message_prefers_display_text_metadata() -> None:
    """When the serving layer augments the LLM-input content (e.g. prepending
    file context), user.message must log what the user actually typed/saw —
    read from Message.metadata["display_text"], not the augmented content."""
    agent = ReActAgent("assistant", model=_StubLLM())
    async with Runtime() as rt:
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="[file context]\n\n---\n\nsummarize this")],
                )
            ),
            metadata={
                "display_text": "summarize this",
                "attachments": [{"id": "a1", "name": "report.pdf"}],
            },
        )
        run_id = await rt.submit(agent.id, msg, max_retries=0)
        async for entry in rt.event_log.tail(run_id):
            if entry.kind == "run.completed":
                break
        entries = await _log_kinds(rt, run_id)

    user_messages = [p for k, p in entries if k == "user.message"]
    assert len(user_messages) == 1
    assert user_messages[0] == {
        "text": "summarize this",
        "attachments": [{"id": "a1", "name": "report.pdf"}],
    }


async def test_orchestrator_also_logs_user_message() -> None:
    """OrchestratorAgent gets the same treatment as ReActAgent — both are
    top-level agent types users interact with directly."""
    from substrate.agents.core.orchestrator import OrchestratorAgent

    agent = OrchestratorAgent("coordinator", model=_StubLLM())
    async with Runtime() as rt:
        result = await rt.run(agent, "Plan a trip")
        entries = await _log_kinds(rt, result.run_id)

    user_messages = [p for k, p in entries if k == "user.message"]
    assert len(user_messages) == 1
    assert user_messages[0]["text"] == "Plan a trip"


async def test_exactly_one_user_message_per_inbound_message() -> None:
    """Not one per middleware/hook stage — log_once must dedupe correctly."""
    agent = ReActAgent("assistant", model=_StubLLM())
    async with Runtime() as rt:
        result = await rt.run(agent, "hello")
        entries = await _log_kinds(rt, result.run_id)

    assert sum(1 for k, _ in entries if k == "user.message") == 1
