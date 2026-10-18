# 0005 — One governed object store; the agent server holds no storage credentials

- **Status:** accepted, 2026-10-18.

## Decision

In production there is exactly one object store, operated through `substrate-data`. The agent server obtains presigned URLs from it and never holds S3 keys; `S3FileStore` and the `[s3]` extra are removed. User files live in a dedicated `volumes` bucket that compute workers cannot read, under `tenants/{tenant}/users/{sub}/...` paths whose ownership the data service enforces. Browsers and sandboxes upload and download directly with presigned URLs, so the API never carries file bytes. The folder-based `FileStore` remains for development and single-node use.

## Consequences

Storage governance (grants, quotas, audit, retention, erasure) is implemented once. A compromised agent host cannot read storage directly.
