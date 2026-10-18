# 0003 — Open-core monorepo and a private control plane

- **Status:** accepted, 2026-10-18.

## Context

Four repositories grew separately: the agent library and server (`agent-substrate`), the data platform (`substrate-data`), the chat UI (`substrate-ui`) and the SaaS site (`agent-substrate-platform`). One change to a data API now means coordinated edits to the API, its client, the agent adapter and a UI, in several repos with version pins between them. The code will keep growing, and anyone must be able to self-host the open parts.

## Decision

- **One public monorepo, named `agent-substrate`** ("agents + substrate"), holds everything a self-hoster runs:
  `agents/` (the agent library, the platform server, the chat UI),
  `data/` (the data platform, its client and web UI, the document and embedding services),
  `identity/`, `contracts/` (versioned OpenAPI), `deploy/` (compose, Helm, Terraform), `docs/adr/`.
- **One private repo, `agent-substrate-platform`,** is the commercial control plane (accounts, billing, projects, provisioning, fleet upgrades). It consumes **released** artifacts (images, chart, client SDKs) by version, never by source path, and is never in a customer's data path (ADR 0006).
- **One engineering system** across the monorepo: a uv workspace (single `uv.lock`) and a pnpm workspace (single `pnpm-lock.yaml`); trunk-based development; conventional commits; per-package releases with release-please (tags `<package>-vX.Y.Z`); path-filtered CI that includes dependents behind a single required `ci-ok` check; CODEOWNERS per domain.
- **Packages stay separately publishable.** `agent-substrate` and `substrate-data-client` go to PyPI on their own versions. The PyPI trusted publisher already points at this repository name.
- `data/document-intelligence` is outside the uv workspace: PaddlePaddle has no Python 3.14 wheel, so it keeps Python 3.13 and its own lock.
- History of the three imported repositories is preserved (`git filter-repo` path moves; per-path commit counts verified equal before and after).

## Consequences

- Cross-cutting changes are one change. CI cost is bounded by path filters.
- The private platform and the public components evolve independently and meet only at versioned contracts (ADR 0004, 0006).
- Splitting a package out again later is a `git filter-repo --subdirectory-filter`.
