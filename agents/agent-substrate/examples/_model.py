"""The model the examples talk to.

``pick_model(...)`` returns a real OpenAI model when ``OPENAI_API_KEY`` is set, and otherwise a scripted one that
replays the replies you give it — so every example runs end to end with no key and no network. Set
``SUBSTRATE_EXAMPLES_OFFLINE=1`` to force the scripted model even when a key is present.

``ScriptedModel`` is also the smallest possible ``ChatModel``: the engine asks a model for four things
(``model``, ``capabilities``, ``generate``/``generate_stream``, ``count_tokens``), and nothing else. Your own
provider is a class with those, passed in as ``model=`` — no registration anywhere.
"""

from __future__ import annotations

import os

from substrate.models import ChatModel
from substrate.testing.scripted import Reply, ScriptedModel, ToolCall  # noqa: F401  (re-exported for the examples)
from substrate.types import ChatMessage, Role


def last_user_text(messages: list[ChatMessage]) -> str:
    return next((m.text for m in reversed(messages) if m.role == Role.USER), "")


def offline() -> bool:
    return bool(os.environ.get("SUBSTRATE_EXAMPLES_OFFLINE")) or not os.environ.get("OPENAI_API_KEY")


def pick_model(*script: Reply, model: str = "gpt-5.4-mini") -> ChatModel:
    """A real model when a key is set, else a ``ScriptedModel`` that replays ``script``."""
    if offline():
        return ScriptedModel(*script)
    from substrate.integrations.llm import LLMFactory

    return LLMFactory(model, os.environ["OPENAI_API_KEY"]).build()
