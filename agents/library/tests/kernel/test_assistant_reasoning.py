"""``create_assistant_agent(reasoning=…)`` reaches the agent, so a chat request's thinking level changes how the model is called."""

from __future__ import annotations

import pytest
from tests._stores import fs_history

from loamz.agents.kernel.agents.factory import create_assistant_agent
from loamz.agents.kernel.models.protocols import ReasoningEffort
from loamz.agents.testing.scripted import ScriptedModel


@pytest.mark.parametrize("level", [None, ReasoningEffort.OFF, ReasoningEffort.HIGH])
def test_reasoning_level_is_kept_by_the_agent(level: ReasoningEffort | None) -> None:
    agent = create_assistant_agent(
        model_client=ScriptedModel(), history=fs_history(), reasoning=level
    )
    assert agent._reasoning == level
