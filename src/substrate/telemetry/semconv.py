"""Names used in telemetry — one place, so every layer agrees.

The generative-AI spans and metrics follow the OpenTelemetry GenAI semantic
conventions (``gen_ai.*``), which is what LLM observability backends ingest. The
conventions are still evolving upstream; keeping the names here means a change is
one edit. Everything specific to this framework is under ``substrate.*``.

Attributes carry identifiers, sizes, counts and outcomes. They never carry prompt
or response text unless a deployment explicitly turns content capture on (see
``tracing.capture_content``): telemetry is sampled, shipped to third parties and
retained outside the erasure path, so content in it is content that cannot be
deleted.
"""

from __future__ import annotations

# ---- span names ------------------------------------------------------------
SPAN_RUN = "substrate.run"
SPAN_LEASE = "substrate.lease"
SPAN_HANDLER = "substrate.handler"
SPAN_LLM = "gen_ai.chat"
SPAN_TOOL = "gen_ai.execute_tool"
SPAN_SPAWN = "substrate.spawn"

# ---- attributes: framework -------------------------------------------------
RUN_ID = "substrate.run.id"
RUN_AGENT = "substrate.run.agent"
RUN_ATTEMPT = "substrate.run.attempt"
RUN_OUTCOME = "substrate.run.outcome"
RUN_TENANT = "substrate.tenant.id"
RUN_THREAD = "substrate.thread.id"
RUN_PARENT = "substrate.run.parent_id"
LEASE_WORKER = "substrate.lease.worker"
LEASE_EPOCH = "substrate.lease.epoch"
REPLAYED = "substrate.replayed"
SUSPENDED = "substrate.run.suspended"
TOOL_OUTCOME = "substrate.tool.outcome"
TOOL_RISK = "substrate.tool.risk"
LLM_COST_USD = "substrate.llm.cost_usd"
ERROR_CODE = "substrate.error.code"
ERROR_RETRYABLE = "substrate.error.retryable"

# ---- attributes: GenAI conventions -----------------------------------------
GEN_AI_OPERATION = "gen_ai.operation.name"
GEN_AI_PROVIDER = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOKEN_TYPE = "gen_ai.token.type"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"

# Content attributes: set only when content capture is enabled.
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
TOOL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_RESULT = "gen_ai.tool.call.result"

CONTENT_ATTRIBUTES = frozenset(
    {GEN_AI_INPUT_MESSAGES, GEN_AI_OUTPUT_MESSAGES, TOOL_ARGUMENTS, TOOL_RESULT}
)

# ---- metrics ---------------------------------------------------------------
M_RUN_DURATION = "substrate.run.duration"
M_RUNS = "substrate.runs"
M_RETRIES = "substrate.run.retries"
M_SUSPENSIONS = "substrate.run.suspensions"
M_QUEUE_DEPTH = "substrate.queue.depth"
M_LEASE_AGE = "substrate.lease.age"
M_REPLAY_HITS = "substrate.journal.replay_hits"
M_DEAD_LETTERS = "substrate.inbox.dead_letters"
M_TOOL_CALLS = "substrate.tool.calls"
M_TOOL_DURATION = "substrate.tool.duration"
M_LLM_DURATION = "gen_ai.client.operation.duration"
M_LLM_TOKENS = "gen_ai.client.token.usage"
M_LLM_COST = "substrate.llm.cost"
M_LLM_ERRORS = "substrate.llm.errors"
