"""``create_assistant_agent(reasoning=…)`` reaches the agent, so a chat request's thinking level changes how the model is called."""

from __future__ import annotations

import pytest

from substrate.agents.factory import create_assistant_agent
from substrate.models.protocols import ReasoningEffort
from substrate.testing.scripted import ScriptedModel


@pytest.mark.parametrize("level", [None, ReasoningEffort.OFF, ReasoningEffort.HIGH])
def test_reasoning_level_is_kept_by_the_agent(level: ReasoningEffort | None) -> None:
    agent = create_assistant_agent(model_client=ScriptedModel(), reasoning=level)
    assert agent._reasoning == level
