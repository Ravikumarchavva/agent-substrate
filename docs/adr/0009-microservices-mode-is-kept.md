# 0009 — The agent server's microservices mode is kept and migrated

- **Status:** accepted, 2026-10-18 (user decision).

## Context

`agents/substrate-cloud` has a monolith (the only mode deployed) and eleven services that lag it: no documents, knowledge or memory features; `workflow_completed` is never published so a run is never marked complete; the gateway and tool executor call an artifact service that does not exist; only 3 of about 28 domain events have a producer.

## Decision

Keep both modes. The microservices adopt the same verifier and the same `[data]` adapters, the missing services (artifacts, file store) become substrate-data domains, the identity service becomes Keycloak, the event gaps are closed, and the gateway serves the monolith's `/api/v1` contract. The same contract and end-to-end suites run in `mode=monolith` and `mode=microservices`.

## Consequences

More work in the identity and data phases. In return the platform can scale components independently in large cells.

## Outcome (2026-10-18)

- The identity service is removed (Keycloak). There is no artifact or file-store service: the gateway and tool executor read and write the caller's own file store (substrate-data's Files domain when the cell has one), with the same file records as the monolith.
- The live stream serves the run's durable journal, the stream the monolith writes, instead of a separate event projector. Seven events remain (thread created or deleted, run started, completed, failed or cancelled, cancel requested), each produced and consumed, and the run's end is published by the agent runtime so the job controller finishes the run.
- Services find each other with `SERVICE_URL_TEMPLATE` (compose and the chart set it); see `deploy/PORTS.md`.
- `agents/substrate-cloud/tests/test_agent_api_modes.py` runs the key contract paths in both modes in one process. Features outside those paths stay monolith-only for now; `services/README.md` lists them.
