"""Tests for the memory system — RedisHistoryProvider lifecycle and storage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ravi.kernel.messages import UserMessage, AssistantMessage, SystemMessage


class TestMessageTypes:
    """Test message content format constraints."""

    def test_system_message_is_string(self) -> None:
        msg = SystemMessage(content="You are helpful.")
        assert isinstance(msg.content, str)
        assert msg.content == "You are helpful."

    def test_user_message_is_list(self) -> None:
        msg = UserMessage(content=[{"type": "text", "text": "Hello"}])
        assert isinstance(msg.content, list)
        assert msg.content[0] == "Hello"

    def test_assistant_message_is_list(self) -> None:
        msg = AssistantMessage(content=["Hi there"], finish_reason="stop")
        assert isinstance(msg.content, list)
        assert msg.content[0] == "Hi there"

    def test_assistant_message_none_content(self) -> None:
        """AssistantMessage content can be None (tool-call-only turn)."""
        msg = AssistantMessage(content=None, finish_reason="tool_calls")
        assert msg.content is None


class TestRedisHistoryProvider:
    """Test RedisHistoryProvider with mocked Redis."""

    @pytest.mark.asyncio
    async def test_history_lifecycle(self) -> None:
        """Test connect → load → disconnect cycle."""
        from ravi.capabilities.memory.redis_history import RedisHistoryProvider

        with patch("ravi.capabilities.memory.redis_history.aioredis") as mock_redis:
            mock_conn = AsyncMock()
            mock_conn.ping = AsyncMock()
            mock_conn.lrange = AsyncMock(return_value=[])
            mock_conn.rpush = AsyncMock()
            mock_conn.expire = AsyncMock()
            mock_conn.aclose = AsyncMock()
            mock_redis.from_url.return_value = mock_conn

            provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0")
            await provider.connect()

            # Should start empty
            msgs = await provider.load_messages("test-123")
            assert isinstance(msgs, list)
            assert msgs == []

            await provider.disconnect()
