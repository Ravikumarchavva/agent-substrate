"""Names used in telemetry — one place, so every layer agrees.

The generative-AI spans and metrics follow the OpenTelemetry GenAI semantic
conventions (``gen_ai.*``), which is what LLM observability backends ingest. The
conventions are still evolving upstream; keeping the names here means a change is
one edit. Everything specific to this framework is under ``loamz.*``.

Attributes carry identifiers, sizes, counts and outcomes. They never carry prompt
or response text unless a deployment explicitly turns content capture on (see
``tracing.capture_content``): telemetry is sampled, shipped to third parties and
retained outside the erasure path, so content in it is content that cannot be
deleted.
"""

from __future__ import annotations

# ---- span names ------------------------------------------------------------
SPAN_RUN = "loamz.run"
SPAN_LEASE = "loamz.lease"
SPAN_HANDLER = "loamz.handler"
SPAN_LLM = "gen_ai.chat"
SPAN_TOOL = "gen_ai.execute_tool"
SPAN_SPAWN = "loamz.spawn"

# ---- attributes: framework -------------------------------------------------
RUN_ID = "loamz.run.id"
RUN_AGENT = "loamz.run.agent"
RUN_ATTEMPT = "loamz.run.attempt"
RUN_OUTCOME = "loamz.run.outcome"
RUN_TENANT = "loamz.tenant.id"
RUN_THREAD = "loamz.thread.id"
RUN_PARENT = "loamz.run.parent_id"
LEASE_WORKER = "loamz.lease.worker"
LEASE_EPOCH = "loamz.lease.epoch"
REPLAYED = "loamz.replayed"
SUSPENDED = "loamz.run.suspended"
TOOL_OUTCOME = "loamz.tool.outcome"
TOOL_RISK = "loamz.tool.risk"
LLM_COST_USD = "loamz.llm.cost_usd"
ERROR_CODE = "loamz.error.code"
ERROR_RETRYABLE = "loamz.error.retryable"

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
M_RUN_DURATION = "loamz.run.duration"
M_RUNS = "loamz.runs"
M_RETRIES = "loamz.run.retries"
M_SUSPENSIONS = "loamz.run.suspensions"
M_QUEUE_DEPTH = "loamz.queue.depth"
M_LEASE_AGE = "loamz.lease.age"
M_REPLAY_HITS = "loamz.journal.replay_hits"
M_DEAD_LETTERS = "loamz.inbox.dead_letters"
M_TOOL_CALLS = "loamz.tool.calls"
M_TOOL_DURATION = "loamz.tool.duration"
M_LLM_DURATION = "gen_ai.client.operation.duration"
M_LLM_TOKENS = "gen_ai.client.token.usage"
M_LLM_COST = "loamz.llm.cost"
M_LLM_ERRORS = "loamz.llm.errors"
