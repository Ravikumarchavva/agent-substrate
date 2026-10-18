# 0006 — The cell is the deployable unit; the control plane is never in the data path

- **Status:** accepted, 2026-10-18.

## Context

The product must run four ways: shared multi-tenant SaaS on our infrastructure, a dedicated deployment per enterprise customer, in the customer's own cloud (BYOC), and self-hosted by anyone without the commercial platform.

## Decision

A **cell** is one complete, identical deployment of the open-source stack: Keycloak, the agent server (monolith or microservices mode), substrate-data (control plane, controller, workers), the two web UIs, PostgreSQL, Redis and S3-compatible storage, shipped as one Helm chart and one compose bundle. The four hosting models differ only in where a cell runs and how many tenants (Keycloak Organizations) it serves; tenancy is already first-class in the code.

The commercial **control plane** (private repo) provisions cells, manages accounts, subscriptions and entitlements, rolls out upgrades, and reads usage. It talks to cells only through a versioned **cell-admin API** with a service account. It is never in a request path and never touches customer data, which is what makes BYOC possible. Developer API keys and public chatbots therefore live **inside the cell**; the platform only configures them.

## Consequences

One artifact to test and secure. Self-hosters and customers run the same thing we do. BYOC needs a small pull-based agent for health and upgrades, not data access.
