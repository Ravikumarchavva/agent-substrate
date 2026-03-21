# Role and Responsibility Matrix

Status: Draft for approval
Scope: Standardized platform roles for microservice operation and governance

## 1. Platform Roles

- platform_admin: manages cluster-wide controls, service onboarding, global policy templates.
- tenant_admin: manages tenant-level users, quotas, integration allow-lists.
- workspace_admin: manages workspace-level roles, tool visibility, workflow settings.
- operator: manages run-time controls, replay/cancel/retry, incident response.
- developer: builds services, tools, contracts, and migrations.
- analyst: reads audit and usage data, cannot mutate production policy.
- end_user: runs conversations and approvals within granted scope.
- service_runtime: machine identity for service-to-service communication.

## 2. Responsibility Matrix by Service

| Service | Primary Role Owner | Secondary Stakeholders | Core Responsibility |
|---|---|---|---|
| Gateway BFF | developer | operator | Edge contract stability, request composition, stream routing |
| Identity Auth | platform_admin | developer | Authentication, identity, token/session lifecycle |
| Policy Authorization | platform_admin | tenant_admin | Authorization decisions, policy enforcement model |
| Conversation Service | developer | workspace_admin | Conversation metadata and query lifecycle |
| Workflow Orchestrator | operator | developer | Durable run state, retries, pause/resume, cancellation |
| Agent Runtime | developer | operator | Model calls, planner execution, runtime constraints |
| Tool Executor | developer | platform_admin | Tool execution isolation, timeout and quota enforcement |
| HITL Approval | workspace_admin | operator | Human approval lifecycle and response governance |
| Artifact Service | workspace_admin | developer | File ingestion, storage metadata, retention controls |
| MCP Registry Gateway | tenant_admin | developer | Integration catalog and capability lifecycle |
| Stream Projection | developer | operator | Durable event projection to client stream channels |
| Admin Control Plane | platform_admin | tenant_admin | Admin APIs, diagnostics, operational controls |

## 3. Action Permissions

| Action | platform_admin | tenant_admin | workspace_admin | operator | developer | analyst | end_user | service_runtime |
|---|---|---|---|---|---|---|---|---|
| manage_global_policies | allow | deny | deny | deny | deny | deny | deny | deny |
| manage_tenant_users | allow | allow | deny | deny | deny | deny | deny | deny |
| manage_workspace_roles | allow | allow | allow | deny | deny | deny | deny | deny |
| submit_conversation | allow | allow | allow | allow | allow | deny | allow | deny |
| approve_hitl_request | allow | allow | allow | allow | deny | deny | allow | deny |
| cancel_or_retry_workflow | allow | allow | allow | allow | deny | deny | deny | allow |
| deploy_or_update_service | allow | deny | deny | deny | allow | deny | deny | deny |
| read_audit_reports | allow | allow | allow | allow | allow | allow | deny | deny |
| execute_tool_call | deny | deny | deny | deny | deny | deny | deny | allow |

## 4. Mandatory Standardization Rules

1. Every API and event must carry tenant_id, workspace_id, actor_id, and role claims.
2. Every service call must authenticate service_runtime identity.
3. Every mutate command must call Policy Authorization before side effects.
4. No service may grant itself elevated permissions; elevation requires policy service result.
5. Admin Control Plane actions must generate immutable audit events.

## 5. Approval Checklist

1. Confirm role names and responsibilities are accepted as canonical.
2. Confirm forbidden actions per role are acceptable.
3. Confirm service_runtime is treated as a first-class principal.
4. Confirm all mutate operations require authorization checks.
