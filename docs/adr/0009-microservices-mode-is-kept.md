# 0009 — The agent server's microservices mode is kept and migrated

- **Status:** accepted, 2026-10-18 (user decision).

## Context

`agents/substrate-cloud` has a monolith (the only mode deployed) and eleven services that lag it: no documents, knowledge or memory features; `workflow_completed` is never published so a run is never marked complete; the gateway and tool executor call an artifact service that does not exist; only 3 of about 28 domain events have a producer.

## Decision

Keep both modes. The microservices adopt the same verifier and the same `[data]` adapters, the missing services (artifacts, file store) become substrate-data domains, the identity service becomes Keycloak, the event gaps are closed, and the gateway serves the monolith's `/api/v1` contract. The same contract and end-to-end suites run in `mode=monolith` and `mode=microservices`.

## Consequences

More work in the identity and data phases. In return the platform can scale components independently in large cells.
