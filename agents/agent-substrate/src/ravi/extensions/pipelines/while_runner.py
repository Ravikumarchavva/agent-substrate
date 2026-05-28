"""WhilePipelineRunner — execute a while-loop pipeline.

The ``while`` node drives a repeating loop:
  - **body** edge: run the downstream agent and feed its output back
    as the next iteration's input.
  - **done** edge: exit the loop and pass the last output downstream.

The loop continues as long as:
  1. The configured ``condition`` Python expression evaluates to truthy, OR
  2. No explicit condition is set (loops until ``max_iterations``).

Config keys on the ``while`` node:
    condition (str): Python expression; receives ``output`` (last agent reply
                     as a string) and ``iteration`` (1-based int).
                     E.g. ``'"DONE" not in output'``.
    max_iterations (int): Hard cap, default 10.
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any, AsyncIterator, Optional

from ravi.kernel.agents.agent_result import AgentRunResult
from ravi.extensions.agents.react.agent import ReActAgent
from ravi.extensions.pipelines._expr_eval import safe_eval

logger = setup_logging()


class WhilePipelineRunner:
    """Wraps a body agent and runs it in a loop until the condition is false."""

    def __init__(
        self,
        body_agent: ReActAgent,
        condition: str = "",
        max_iterations: int = 10,
        done_agent: Optional[ReActAgent] = None,
    ) -> None:
        self.body_agent = body_agent
        self.condition = condition.strip()
        self.max_iterations = max(1, max_iterations)
        self.done_agent = done_agent  # optional agent to call after the loop

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_continue(self, output: str, iteration: int) -> bool:
        """Evaluate the user-supplied condition expression."""
        if not self.condition:
            return True  # loop until max_iterations
        return safe_eval(self.condition, {"output": output, "iteration": iteration})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, user_input: str) -> str:
        """Run the while loop synchronously, return the final output."""
        output: str = user_input
        for i in range(1, self.max_iterations + 1):
            result: AgentRunResult = await self.body_agent.run(output)
            output = result.output_text or ""
            if not self._should_continue(output, i):
                logger.debug("While loop exited at iteration %d", i)
                break
        else:
            logger.debug("While loop reached max_iterations=%d", self.max_iterations)

        if self.done_agent:
            done_result: AgentRunResult = await self.done_agent.run(output)
            output = done_result.output_text or ""
        return output

    async def run_stream(self, user_input: str) -> AsyncIterator[Any]:
        """Streaming variant — yields chunks from each iteration."""
        output = user_input
        for i in range(1, self.max_iterations + 1):
            # Collect streamed chunks and accumulate the full response
            full = []
            async for chunk in self.body_agent.run_stream(output):
                yield chunk
                _content = getattr(chunk, "content", None)
                if _content:
                    full.append(str(_content))
            output = "".join(full)
            if not self._should_continue(output, i):
                break

        if self.done_agent:
            async for chunk in self.done_agent.run_stream(output):
                yield chunk
