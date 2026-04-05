# Data Ownership and Contract Standards

Status: Draft for approval
Scope: single-writer ownership model and contract versioning standards

## 1. Data Ownership Model

| Data Domain | System of Record | Read Replicas/Projections | Notes |
|---|---|---|---|
| users, identities, sessions | Identity Auth | Gateway BFF cache, Admin views | authentication domain only |
| roles, policies, grants | Policy Authorization | all services via decision API/cache | no direct policy DB writes outside service |
| conversations, messages metadata | Conversation Service | Stream Projection, Admin views | append-only events for timeline integrity |
| workflow runs, run states, retries | Workflow Orchestrator | Stream Projection, Admin views | durable execution state source of truth |
| tool invocations, execution logs | Tool Executor | Workflow Orchestrator, Admin views | include tool risk tier and timeout data |
| hitl requests and responses | HITL Approval | Workflow Orchestrator, Stream Projection | approval deadlines and actor identity required |
| artifacts and file metadata | Artifact Service | Conversation Service references | binary in object storage, metadata in DB |
| mcp app registry and capabilities | MCP Registry Gateway | Agent Runtime and Admin views | versioned capability manifests |
| audit events | Admin Control Plane | Analyst views and SIEM export | immutable append-only log model |

## 2. Contract Standards

### API Standards
- Command APIs: must be idempotent using client_request_id.
- Query APIs: must support pagination and deterministic sorting.
- Every endpoint must include contract_version in response envelope.
- Every mutating endpoint must return operation_id for traceability.

### Event Standards
- Event envelope fields: event_id, event_type, event_version, emitted_at, tenant_id, workspace_id, actor_id, correlation_id.
- Event payloads are immutable after publish.
- Breaking payload changes require new event_type or major event_version.
- Consumer services must declare supported event_version ranges.

## 3. Idempotency and Consistency Rules

1. Workflow commands must be idempotent by tenant_id + workspace_id + client_request_id.
2. Tool execution results must include deterministic invocation_id.
3. HITL responses must reject duplicate response attempts after finalization.
4. Stream Projection must be replayable from event backbone offsets.
5. Cross-service writes are forbidden; use command APIs or event-driven update paths.

## 4. Security and Data Rules

1. PII and credential data only in Identity Auth storage domain.
2. Tool secrets are referenced via secret IDs, never embedded in event payloads.
3. Artifact metadata must include ownership and retention policy fields.
4. Audit events must include actor principal and policy decision reference.

## 5. Approval Checklist

1. Confirm each data domain has exactly one owner.
2. Confirm event envelope standard fields are mandatory.
3. Confirm idempotency strategy is accepted for command APIs.
4. Confirm immutable audit requirement is accepted.
