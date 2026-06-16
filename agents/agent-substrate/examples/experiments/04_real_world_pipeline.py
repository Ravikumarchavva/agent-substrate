"""Multi-step research and synthesis workflow.

Demonstrates chaining multiple agent.run() calls where the output of one
step becomes the input context for the next.  This mimics a real-world
research pipeline:

    Step 1 — Search: gather raw information on the topic
    Step 2 — Analyze: identify key themes and claims
    Step 3 — Synthesize: combine findings into a coherent narrative
    Step 4 — Format: produce a structured research summary

Run:
    cd ravi-engine
    uv run python examples/real-world-agent-mimics/experiment.py
"""

import asyncio

from ravi.agents.core import ReActAgent
from ravi.agents.tools.builtin_tools import CalculatorTool, WebSearchTool
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.agents.context import InMemoryHistoryProvider

# Infrastructure: OPENAI_API_KEY environment variable

RESEARCH_TOPIC = "the impact of agentic AI on software engineering workflows in 2025"

# ---


def build_research_agent(step_name: str, instructions: str) -> ReActAgent:
    """Build a single-purpose agent for one pipeline step."""
    catalog = AgentCatalog()
    catalog.register_model("primary", OpenAIClient(model="gpt-4o-mini"))
    catalog.register_memory("memory", InMemoryHistoryProvider())
    catalog.register_tool(WebSearchTool())
    catalog.register_tool(CalculatorTool())

    return ReActAgent(
        name=step_name,
        description=f"Research pipeline agent: {step_name}",
        system_instructions=instructions,
        catalog=catalog,
        verbose=False,
    )


async def step_search(topic: str) -> str:
    """Step 1: Gather raw information about the topic."""
    agent = build_research_agent(
        "searcher",
        (
            "You are a research assistant. Use web_search to find current, "
            "factual information about the given topic.  Return bullet-point "
            "raw findings — do not summarise yet."
        ),
    )
    result = await agent.run(f"Find recent information about: {topic}")
    return result.output_text


async def step_analyze(raw_findings: str) -> str:
    """Step 2: Identify key themes and assess the evidence."""
    agent = build_research_agent(
        "analyst",
        (
            "You are an expert analyst.  You will receive raw research notes. "
            "Identify the 3-5 most important themes and assess the strength of "
            "evidence for each.  Output a numbered list of themes with a brief "
            "evidence assessment."
        ),
    )
    prompt = (
        f"Analyze these research findings and extract key themes:\n\n{raw_findings}"
    )
    result = await agent.run(prompt)
    return result.output_text


async def step_synthesize(themes: str, raw_findings: str) -> str:
    """Step 3: Combine findings and themes into a coherent narrative."""
    agent = build_research_agent(
        "synthesizer",
        (
            "You are a senior researcher.  You will receive key themes and raw "
            "findings.  Write a coherent 3-4 paragraph synthesis that tells the "
            "story of what the research reveals.  Be specific and cite details "
            "from the findings."
        ),
    )
    prompt = (
        f"Synthesize the following into a coherent narrative.\n\n"
        f"KEY THEMES:\n{themes}\n\n"
        f"RAW FINDINGS:\n{raw_findings}"
    )
    result = await agent.run(prompt)
    return result.output_text


async def step_format(topic: str, themes: str, narrative: str) -> str:
    """Step 4: Produce the final structured research summary."""
    agent = build_research_agent(
        "formatter",
        (
            "You are a technical writer.  Format the provided research material "
            "into a clean, structured summary with these sections: "
            "Executive Summary, Key Findings, Detailed Analysis, Conclusion.  "
            "Use clear headings and concise language."
        ),
    )
    prompt = (
        f"Format a research summary for topic: '{topic}'\n\n"
        f"THEMES:\n{themes}\n\n"
        f"NARRATIVE:\n{narrative}"
    )
    result = await agent.run(prompt)
    return result.output_text


# ---


async def main() -> None:
    topic = RESEARCH_TOPIC
    print(f"Research topic: {topic}\n")

    # --- Step 1: Search ---
    print("[1/4] Searching for information...")
    raw_findings = await step_search(topic)

    # --- Step 2: Analyze ---
    print("[2/4] Analyzing key themes...")
    themes = await step_analyze(raw_findings)

    # --- Step 3: Synthesize ---
    print("[3/4] Synthesizing narrative...")
    narrative = await step_synthesize(themes, raw_findings)

    # --- Step 4: Format ---
    print("[4/4] Formatting final report...")
    report = await step_format(topic, themes, narrative)

    # --- Output ---
    print("\n" + "=" * 70)
    print("RESEARCH SUMMARY")
    print("=" * 70)
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
