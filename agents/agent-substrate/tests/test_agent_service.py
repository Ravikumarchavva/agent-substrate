"""Tests for agent_service: _rebuild_messages and load_agent_for_thread.

Uses pytest-asyncio (asyncio_mode = "auto" in pyproject.toml).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from raavan.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from raavan.server.services.agent_service import _rebuild_messages


SYSTEM_PROMPT = "You are a helpful agent."


# ---------------------------------------------------------------------------
# _rebuild_messages — message reconstruction from Postgres rows
# ---------------------------------------------------------------------------


async def test_rebuild_messages_empty_rows_yields_only_system():
    msgs = await _rebuild_messages([], SYSTEM_PROMPT)
    assert len(msgs) == 1
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == SYSTEM_PROMPT


async def test_rebuild_messages_user_and_assistant():
    rows: List[Dict[str, Any]] = [
        {"type": "user_message", "input": "Hello", "metadata": {}},
        {
            "type": "assistant_message",
            "output": "Hi there",
            "metadata": {},
            "generation": {"finish_reason": "stop"},
        },
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    assert len(msgs) == 3
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], UserMessage)
    assert isinstance(msgs[2], AssistantMessage)
    assert msgs[2].finish_reason == "stop"


async def test_rebuild_messages_user_assistant_tool_result_four_messages():
    rows: List[Dict[str, Any]] = [
        {"type": "user_message", "input": "search for X", "metadata": {}},
        {
            "type": "assistant_message",
            "output": None,
            "metadata": {},
            "generation": {
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {"id": "tc-1", "name": "search", "arguments": {"q": "X"}}
                ],
            },
        },
        {
            "type": "tool_result",
            "output": "Result: X found",
            "name": "search",
            "is_error": False,
            "metadata": {"tool_call_id": "tc-1"},
        },
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    # system + user + assistant(tool_calls) + tool_result → 4 messages
    assert len(msgs) == 4
    assert isinstance(msgs[0], SystemMessage)
    assert isinstance(msgs[1], UserMessage)
    assert isinstance(msgs[2], AssistantMessage)
    assert msgs[2].tool_calls is not None
    assert msgs[2].tool_calls[0].name == "search"
    assert isinstance(msgs[3], ToolExecutionResultMessage)
    assert msgs[3].name == "search"
    assert msgs[3].tool_call_id == "tc-1"


async def test_rebuild_messages_skips_duplicate_system_row():
    """A persisted system_message row must not create a second SystemMessage."""
    rows: List[Dict[str, Any]] = [
        {"type": "system_message", "input": "old prompt", "metadata": {}},
        {"type": "user_message", "input": "Hello", "metadata": {}},
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    system_msgs = [m for m in msgs if isinstance(m, SystemMessage)]
    assert len(system_msgs) == 1, "System message must appear exactly once"
    assert system_msgs[0].content == SYSTEM_PROMPT


async def test_rebuild_messages_standalone_tool_call_row_skipped():
    """Standalone tool_call rows are silently dropped (embedded in AssistantMessage)."""
    rows: List[Dict[str, Any]] = [
        {"type": "tool_call", "name": "noop", "input": "{}", "metadata": {}},
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    # Only the prepended system message
    assert len(msgs) == 1
    assert isinstance(msgs[0], SystemMessage)


async def test_rebuild_messages_mcp_app_context_becomes_user_message():
    rows: List[Dict[str, Any]] = [
        {
            "type": "mcp_app_context",
            "name": "spotify_player",
            "output": '{"playing": true}',
            "metadata": {},
        }
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    assert len(msgs) == 2
    user_msg = msgs[1]
    assert isinstance(user_msg, UserMessage)
    # content is built as list[str] per UserMessage contract
    content_text = (
        user_msg.content[0] if isinstance(user_msg.content, list) else user_msg.content
    )
    assert "spotify_player" in content_text
    assert "MCP App Update" in content_text


async def test_rebuild_messages_tool_result_is_error_flag():
    rows: List[Dict[str, Any]] = [
        {
            "type": "tool_result",
            "output": "Something went wrong",
            "name": "run_code",
            "is_error": True,
            "metadata": {"tool_call_id": "tc-err"},
        }
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    tool_result = msgs[1]
    assert isinstance(tool_result, ToolExecutionResultMessage)
    assert tool_result.is_error is True


async def test_rebuild_messages_assistant_none_content():
    """AssistantMessage with only tool_calls and no text output → content=None."""
    rows: List[Dict[str, Any]] = [
        {
            "type": "assistant_message",
            "output": None,
            "metadata": {},
            "generation": {
                "finish_reason": "tool_calls",
                "tool_calls": [{"id": "tc-2", "name": "calc", "arguments": {}}],
            },
        }
    ]
    msgs = await _rebuild_messages(rows, SYSTEM_PROMPT)
    assistant = msgs[1]
    assert isinstance(assistant, AssistantMessage)
    assert assistant.content is None
    assert assistant.tool_calls is not None


# ---------------------------------------------------------------------------
# load_agent_for_thread — Redis hot path (limit= contract)
# ---------------------------------------------------------------------------


async def test_load_agent_hot_path_calls_restore_without_limit():
    """Redis hit → restore() must be called with no limit (full history loaded)."""
    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=True)

    mock_per_request = AsyncMock()
    mock_per_request.restore = AsyncMock(return_value=3)
    mock_per_request.get_messages = AsyncMock(return_value=[])

    with (
        patch(
            "raavan.server.services.agent_service.RedisMemory.for_session",
            return_value=mock_per_request,
        ),
        patch(
            "raavan.server.services.agent_service.create_agent_for_thread",
            return_value=MagicMock(),
        ),
        patch(
            "raavan.server.services.agent_service.load_messages_for_memory",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        from raavan.server.services.agent_service import (
            load_agent_for_thread,
        )

        db = AsyncMock()
        await load_agent_for_thread(
            db,
            uuid.uuid4(),
            model_client=MagicMock(),
            tools=[],
            system_instructions="System",
            redis_memory=mock_redis,
            model_context_window=40,
        )
        # No limit — Redis holds the full history; SlidingWindowContext
        # is what filters messages at LLM-call time.
        mock_per_request.restore.assert_called_once_with()


async def test_load_agent_cold_path_seeds_redis_with_all_messages():
    """Redis miss → store_many called with system+user+assistant=3 messages."""
    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=False)
    mock_redis.store_many = AsyncMock()

    mock_per_request = AsyncMock()
    mock_per_request.restore = AsyncMock(return_value=0)
    mock_per_request.get_messages = AsyncMock(return_value=[])

    system_prompt = "You are helpful."
    rows = [
        {"type": "user_message", "input": "Hello", "metadata": {}},
        {
            "type": "assistant_message",
            "output": "Hi",
            "metadata": {},
            "generation": {"finish_reason": "stop"},
        },
    ]

    with (
        patch(
            "raavan.server.services.agent_service.RedisMemory.for_session",
            return_value=mock_per_request,
        ),
        patch(
            "raavan.server.services.agent_service.load_messages_for_memory",
            new_callable=AsyncMock,
            return_value=rows,
        ),
        patch(
            "raavan.server.services.agent_service.create_agent_for_thread",
            return_value=MagicMock(),
        ),
    ):
        from raavan.server.services.agent_service import (
            load_agent_for_thread,
        )

        db = AsyncMock()
        await load_agent_for_thread(
            db,
            uuid.uuid4(),
            model_client=MagicMock(),
            tools=[],
            system_instructions=system_prompt,
            redis_memory=mock_redis,
            model_context_window=40,
        )
        # store_many must be called with (session_id, messages)
        mock_redis.store_many.assert_called_once()
        call_args = mock_redis.store_many.call_args
        _, stored_messages = call_args.args
        # system(prepended) + user + assistant = 3
        assert len(stored_messages) == 3
        assert isinstance(stored_messages[0], SystemMessage)
        assert isinstance(stored_messages[1], UserMessage)
        assert isinstance(stored_messages[2], AssistantMessage)


async def test_load_agent_no_redis_uses_unbounded_memory():
    """When redis_memory=None, agent falls back to Postgres-only UnboundedMemory."""
    from raavan.server.services.agent_service import load_agent_for_thread
    from raavan.core.memory.unbounded_memory import UnboundedMemory

    captured_memory = {}

    def _capture_agent(**kwargs):
        captured_memory["mem"] = kwargs.get("memory")
        return MagicMock()

    rows = [
        {"type": "user_message", "input": "Hi", "metadata": {}},
    ]

    with (
        patch(
            "raavan.server.services.agent_service.load_messages_for_memory",
            new_callable=AsyncMock,
            return_value=rows,
        ),
        patch(
            "raavan.server.services.agent_service.create_agent_for_thread",
            side_effect=_capture_agent,
        ),
    ):
        db = AsyncMock()
        await load_agent_for_thread(
            db,
            uuid.uuid4(),
            model_client=MagicMock(),
            tools=[],
            system_instructions="Sys",
            redis_memory=None,
        )

    assert isinstance(captured_memory["mem"], UnboundedMemory)


# ---------------------------------------------------------------------------
# Single-flight lock semantics (mirrors the 409 guard in chat.py)
# ---------------------------------------------------------------------------


async def test_single_flight_locked_lock_reports_as_locked():
    """An acquired Lock must report locked() == True."""
    lock = asyncio.Lock()
    await lock.acquire()
    assert lock.locked() is True


async def test_single_flight_second_acquire_blocks():
    """When a Lock is held, a second acquire attempt must time out."""
    lock = asyncio.Lock()
    await lock.acquire()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(lock.acquire(), timeout=0.05)


async def test_single_flight_released_lock_is_acquirable():
    """After release(), a second caller must be able to acquire the lock."""
    lock = asyncio.Lock()
    await lock.acquire()
    lock.release()
    # Should not block
    await asyncio.wait_for(lock.acquire(), timeout=0.1)
    assert lock.locked() is True
