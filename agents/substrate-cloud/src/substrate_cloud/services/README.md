# Microservices

Nine FastAPI applications, one per folder, that together serve the monolith's Agent API (`contracts/agents/v1`) behind a single
public gateway ([ADR 0009](../../../../../docs/adr/0009-microservices-mode-is-kept.md)). `SUBSTRATE_MODE=microservices` plus
`SUBSTRATE_SERVICE=<name>` picks which one a container runs; ports are in `deploy/PORTS.md`.

| service | owns | talks to |
| --- | --- | --- |
| `gateway` (8001) | the public `/api/v1` routes; the caller's files (their own file store) | every service below |
| `policy` (8011) | role rules and workspace grants; "may this role submit a conversation?" | |
| `conversation` (8012) | threads (owner, tenant, name, pins), feedback; messages are projected from the run journal | |
| `job_controller` (8013) | the run's lifecycle: pending, running, completed, failed, cancelled | conversation, agent_runtime |
| `agent_runtime` (8014) | builds the thread's agent and runs it on the runtime; announces how the run ended | |
| `tool_executor` (8015) | runs a tool for a caller; files a code interpreter produces go to the caller's file store | conversation |
| `human_gate` (8016) | answers a pending approval or question; reads what a run waits on | conversation |
| `live_stream` (8017) | the run's wire events as server-sent events, from the run's journal | conversation |
| `admin` (8019) | the audit trail, written from domain events; platform administrators only | |

The identity service is gone: Keycloak signs users in and issues the tokens.

## One record of what happened

The runtime's run journal (`rt_runs` / `rt_events` in the shared store) is the conversation. `agent_runtime` writes it,
`live_stream` tails it (`hello`, `text.delta`, `run.completed`, `[DONE]`: the same stream the monolith writes), `conversation` projects
messages from it, and `human_gate` signals the run suspended in it. There is no second table of messages or second event vocabulary.

## Identity

Every router of every service sits behind `AUTHENTICATED` (`base.py`): a Keycloak access token verified by `substrate-identity`
(RS256, audience `agent-substrate`, one organization, see ADR 0007). The gateway forwards the caller's own token, and each service
reads tenant and user from it: nothing is taken from a request body, and what is not the caller's is a 404. Errors are RFC 9457
`application/problem+json`, relayed with their original status when they come from a peer; an unreachable peer is a 502.
`tests/test_oidc_entry_points.py` proves, for every route of every service, that HS256, wrong-audience, ID, expired, foreign-issuer and
missing tokens are refused. Only `/health` and `/health/ready` are open.

## Domain events

Seven events cross a service boundary, each with a producer and a consumer (`shared/events/types.py` has the table):
`conversation.thread_created|deleted` and `workflow.run_started` (audit), `workflow.run_completed|failed|cancelled` from the runtime
(the job controller finishes the run, admin audits it) and `job.cancel_requested` (the runtime stops the run). `tests/test_microservices_mode.py`
fails when an event has no producer or no consumer.

## Testing

`tests/test_agent_api_modes.py` runs the key contract paths (token refused, threads, chat run and stream, files) against the monolith
and against these services, in one process. `make smoke` walks the same paths without pytest.
All services are paths under `/api/v1`; the gateway adds nothing to the public contract.

## Not in microservices mode yet

Documents, knowledge bases, memory, agents and groups, scheduled tasks, triggers, pipelines, the approvals inbox, task boards, branches,
workspace and the rate limiter and per-tenant message entitlement are monolith routes only. The agent runtime has the web surfer
and does not call the tool executor, so a chat in this mode cannot run code. A tool approval is answered through the run's durable signal,
but the runtime builds no approval handler yet, so a risky tool is not paused for one.
