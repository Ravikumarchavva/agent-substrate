"""Lifecycle hooks for the chat server.

Provides a registry for Chainlit-inspired hooks that run at different
stages of a chat session. Application code registers callbacks via
decorators; the server calls them at the appropriate time.

Hooks:
  on_chat_start  – new thread created, agent not yet invoked
  on_message     – user sent a message, before agent processes it
  on_chat_end    – thread explicitly closed / cleaned up
  on_chat_resume – existing thread loaded from DB (reconnect)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from raavan.core.agents.base_agent import BaseAgent

logger = logging.getLogger("raavan.server.hooks")


# ── Context objects passed to hooks ──────────────────────────────────────────


@dataclass
class ChatContext:
    """Context object passed to every hook."""

    thread_id: uuid.UUID
    db: AsyncSession
    agent: BaseAgent  # decoupled from concrete ReActAgent
    user_id: Optional[uuid.UUID] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── Hook type aliases ────────────────────────────────────────────────────────

StartHook = Callable[[ChatContext], Awaitable[None]]
MessageHook = Callable[[ChatContext, str], Awaitable[None]]
EndHook = Callable[[ChatContext], Awaitable[None]]
ResumeHook = Callable[[ChatContext], Awaitable[None]]


# ── Registry ─────────────────────────────────────────────────────────────────


class HookRegistry:
    """Central registry for lifecycle hooks.

    Usage::

        hooks = HookRegistry()

        @hooks.on_chat_start
        async def setup(ctx: ChatContext):
            print(f"Thread {ctx.thread_id} started!")

        @hooks.on_message
        async def before_msg(ctx: ChatContext, content: str):
            print(f"User said: {content}")
    """

    def __init__(self) -> None:
        self._on_chat_start: list[StartHook] = []
        self._on_message: list[MessageHook] = []
        self._on_chat_end: list[EndHook] = []
        self._on_chat_resume: list[ResumeHook] = []

    # ── Decorators ───────────────────────────────────────────────────────

    def on_chat_start(self, fn: StartHook) -> StartHook:
        """Register a hook that fires when a new thread is created."""
        self._on_chat_start.append(fn)
        return fn

    def on_message(self, fn: MessageHook) -> MessageHook:
        """Register a hook that fires before the agent processes a message."""
        self._on_message.append(fn)
        return fn

    def on_chat_end(self, fn: EndHook) -> EndHook:
        """Register a hook that fires when a thread is closed."""
        self._on_chat_end.append(fn)
        return fn

    def on_chat_resume(self, fn: ResumeHook) -> ResumeHook:
        """Register a hook that fires when a thread is resumed from DB."""
        self._on_chat_resume.append(fn)
        return fn

    # ── Dispatch ─────────────────────────────────────────────────────────

    @staticmethod
    async def _safe_call(fn: Callable, *args: Any) -> None:
        """Invoke *fn* and swallow exceptions so one bad hook can't crash chat."""
        try:
            await fn(*args)
        except Exception:
            logger.exception(
                "Unhandled exception in hook %s",
                getattr(fn, "__qualname__", repr(fn)),
            )

    async def fire_chat_start(self, ctx: ChatContext) -> None:
        await asyncio.gather(
            *[self._safe_call(hook, ctx) for hook in self._on_chat_start],
            return_exceptions=True,
        )

    async def fire_message(self, ctx: ChatContext, content: str) -> None:
        await asyncio.gather(
            *[self._safe_call(hook, ctx, content) for hook in self._on_message],
            return_exceptions=True,
        )

    async def fire_chat_end(self, ctx: ChatContext) -> None:
        await asyncio.gather(
            *[self._safe_call(hook, ctx) for hook in self._on_chat_end],
            return_exceptions=True,
        )

    async def fire_chat_resume(self, ctx: ChatContext) -> None:
        await asyncio.gather(
            *[self._safe_call(hook, ctx) for hook in self._on_chat_resume],
            return_exceptions=True,
        )


# Global default registry
hooks = HookRegistry()
