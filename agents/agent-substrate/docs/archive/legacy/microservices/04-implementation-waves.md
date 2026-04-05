# Implementation Waves and Approval Gates

Status: Draft for approval
Scope: phased extraction into Kubernetes microservices

## Wave 0: Planning and Contract Freeze

Goals:
- Approve topology, service catalog, role matrix, and data ownership.
- Freeze API and event contract baseline for Wave 1.

Exit criteria:
- Approved docs in this folder.
- Initial contract test scaffolding defined.

## Wave 1: Identity, Policy, and Gateway Split

Services:
- Identity Auth
- Policy Authorization
- Gateway BFF

Changes:
- Move auth/session responsibilities out of app routes into Identity Auth.
- Route all client calls through Gateway BFF.
- Enforce policy decision checks on mutating operations.

Exit criteria:
- End-to-end login and authorization through dedicated services.
- No direct frontend calls to internal runtime services.

## Wave 2: Conversation and Workflow Separation

Services:
- Conversation Service
- Workflow Orchestrator

Changes:
- Move thread/message lifecycle into Conversation Service.
- Move run state, retries, cancellation, and resume logic into Workflow Orchestrator.

Exit criteria:
- Durable run state independent of pod-local memory.
- Restart-safe and replay-safe workflow commands.

## Wave 3: Agent Runtime and Tool Executor

Services:
- Agent Runtime
- Tool Executor
- MCP Registry Gateway

Changes:
- Move planner/model loop into Agent Runtime.
- Move tool invocation and risk-tier handling into Tool Executor.
- Standardize integration manifest handling in MCP Registry.

Exit criteria:
- Agent execution and tool execution separated by contracts.
- Tool invocations are auditable and policy-governed.

## Wave 4: HITL, Stream Projection, and Artifact Service

Services:
- HITL Approval
- Stream Projection
- Artifact Service

Changes:
- Move approval lifecycle to durable HITL service.
- Replace direct in-memory stream flows with event projections.
- Move file ingestion and retention controls to Artifact Service.

Exit criteria:
- Approval state survives disconnect/reconnect and restarts.
- Stream channels can replay from durable events.

## Wave 5: Admin Control Plane and Operations Hardening

Services:
- Admin Control Plane
- Observability pipeline integrations

Changes:
- Add admin APIs for tenant and runtime operations.
- Standardize SLOs, tracing, metrics, logs, and audit exports.
- Add chaos and recovery validation for critical workflows.

Exit criteria:
- Operational dashboards and runbooks for all critical paths.
- Security and reliability sign-off for production rollout.

## Cross-Wave Non-Negotiables

1. No breaking contract change without version increment.
2. No shared database writes across services.
3. No correctness-critical state stored only in process memory.
4. Every wave must include policy and audit validation.
5. Every wave must include rollback strategy.

## Final Approval Gate

1. Topology approved.
2. Roles approved.
3. Data ownership approved.
4. Contract standards approved.
5. Wave sequence approved.
6. Implementation can start only after all five approvals are complete.
