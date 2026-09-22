"""The fixed researcher/calculator/clock/coordinator topology used when
``AGENT_MODE=orchestrator``.

Moved out of ``agents/factory.py`` — this is serving configuration wearing
an agent-construction disguise (a fixed demo topology, sole caller
``serving_factory.py``), not general-purpose L1 agent-building API.
``infrastructure/`` is the orthogonal layer that's allowed to know about
concrete serving-facing shapes like this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from substrate.agents.context.compaction.presets import build_token_budget_pipeline
from substrate.agents.factory import create_assistant_agent
from substrate.agents.storage.local_history import LocalFilesystemHistoryProvider
from substrate.kernel.llm import LLMClient
from substrate.kernel import Tool

if TYPE_CHECKING:
    from substrate.agents.core import ReActAgent, OrchestratorAgent


@dataclass
class ResearchOrchestrator:
    """The coordinator plus its three sub-agents — all four must be
    registered with the Runtime before submitting work to the coordinator."""

    coordinator: "OrchestratorAgent"
    sub_agents: list["ReActAgent"]

    @property
    def all_agents(self) -> list["ReActAgent | OrchestratorAgent"]:
        return [self.coordinator, *self.sub_agents]


def build_research_orchestrator(
    *,
    model_client: LLMClient,
    researcher_tools: list[Tool],
    calculator_tools: list[Tool],
    clock_tools: list[Tool],
) -> ResearchOrchestrator:
    """Build the fixed researcher/calculator/clock/coordinator topology used
    when ``AGENT_MODE=orchestrator``.

    Tools are passed in rather than constructed here — agents/ must not
    import capabilities/ (``WebSearchTool``, ``CalculatorTool``, etc. all
    live in ``capabilities/tools/``), so the caller (``infrastructure/
    serving_factory.py``, which is allowed to cross both layers) builds the
    concrete tool instances and hands them to each specialist.

    Returned unregistered, like ``create_assistant_agent`` — the caller
    (which owns the ``Runtime``) is responsible for registering every agent
    in ``.all_agents`` before submitting work to the coordinator.
    """
    from substrate.agents.core import OrchestratorAgent, SubAgentConfig
    from substrate.agents.context import ContextConfig

    researcher = create_assistant_agent(
        name="researcher",
        model_client=model_client,
        tools=researcher_tools,
        system_instructions="You are a research specialist.",
        model_context=build_token_budget_pipeline(),
        max_iterations=5,
    )
    calculator = create_assistant_agent(
        name="calculator",
        model_client=model_client,
        tools=calculator_tools,
        system_instructions="You are a calculation specialist.",
        model_context=build_token_budget_pipeline(),
        max_iterations=3,
    )
    clock = create_assistant_agent(
        name="clock",
        model_client=model_client,
        tools=clock_tools,
        system_instructions="You are a time specialist.",
        model_context=build_token_budget_pipeline(),
        max_iterations=2,
    )
    orchestrator = OrchestratorAgent(
        "coordinator",
        model=model_client,
        sub_agents=[
            SubAgentConfig(
                researcher, description="Searches the web.", ask_timeout=60.0
            ),
            SubAgentConfig(
                calculator, description="Performs calculations.", ask_timeout=30.0
            ),
            SubAgentConfig(
                clock, description="Reports the current time.", ask_timeout=10.0
            ),
        ],
        max_iterations=15,
        context=ContextConfig(
            LocalFilesystemHistoryProvider(), pipeline=build_token_budget_pipeline()
        ),
    )
    # Display names only — Actor routing keys stay lowercase (unchanged
    # from before this was extracted from infrastructure/serving_factory.py).
    researcher.name = "Researcher"
    calculator.name = "Calculator"
    clock.name = "Clock"
    orchestrator.name = "Coordinator"
    return ResearchOrchestrator(
        coordinator=orchestrator, sub_agents=[researcher, calculator, clock]
    )


__all__ = ["ResearchOrchestrator", "build_research_orchestrator"]
