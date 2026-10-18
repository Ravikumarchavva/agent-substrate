# European data-protection controls

This page maps what the code does to the requirements European deployments are usually asked about. It lists
**technical controls and their limits**. It is not legal advice and the software cannot be "GDPR compliant" on its
own: purpose, lawful basis, DPIA, processor contracts and transfer mechanisms are organisational and are yours.

## Controls

| Requirement | Control here | Limit |
| --- | --- | --- |
| **GDPR Art. 5(1)(c)** data minimisation | Agent tokens are read-only and tenant-scoped. Views give aggregated or filtered slices without raw-table access. Row, byte and time limits on every statement; the sandbox client refuses to hold more than 512 MiB. | Row filters and column masks per reader (`OPERATIONS.md`); views for shared aggregates. |
| **Art. 5(1)(e)** storage limitation | Audit events and statement history are deleted after `SUBSTRATE_DATA_HISTORY_RETENTION_DAYS` (default 365; `0` keeps them). | Table data retention is yours: `:expire` removes old snapshots and their files on request (not on a schedule). |
| **Art. 5(1)(f), 32** integrity and confidentiality | TLS 1.3 by default (1.2 only with ECDHE + AEAD suites), optional mutual TLS, no plaintext on a routable address, short-lived signed tokens, single-use 60 s query tickets, a sandbox with no network, a locked-down query engine. | Encryption at rest is the storage layer's (SeaweedFS/disk/KMS); with credential vending on, SQL workers hold no storage key (batch jobs still do). |
| **Art. 25** by design and by default | Default deny; objects without a privilege answer `404`; agent tokens can never be admin; agent data access is off until configured. | |
| **Art. 30** records of processing | Every statement and administrative action is recorded with user, acting agent, tenant, thread, agent id, **token id**, tables read and outcome. | |
| **Art. 17** erasure | Audit and statement history expire. | Row-level deletion in Iceberg tables is not exposed yet. |
| **Art. 28, 44–49** processors and transfers | Each service calls only addresses you configure; the sandbox gateway has one destination and an allow-list; nothing phones home. Run both services in the regions you need. | **Query results an agent prints become model context.** If the model provider is outside the EU/EEA, that is a transfer. Choose an EU-hosted model or keep personal data out of results (views). |
| **NIS2 Art. 21, DORA** access control, cryptography, logging, key management | Role-based grants; verified TLS and mTLS; audit trail; key rotation with no coordination (`data-token.previous.pem`); issuer-outage tolerance that fails closed after an hour. | SIEM export (NDJSON/CEF), per-principal rate, concurrency and scan limits, mTLS between all services. No HSM/KMS integration for the token-signing key. |
| **EU AI Act Art. 12, 26(6)** logging and traceability | Each query ties to a user, an agent, a thread and a token, kept at least 6 months by default. | Hash-chained and exportable; tamper-evident, not WORM: a database administrator can rewrite the whole tail, so ship the export to storage you do not control. |

## Cryptography

Transport: TLS 1.3 (the server) and 1.2 or newer (the gateway client) with certificate verification always on.
Tokens and query tickets: Ed25519. Check the algorithms against the guidance your regulator applies (for example
national technical guidelines such as BSI TR-02102-2 for TLS); configure `SUBSTRATE_DATA_TLS_MIN_VERSION`
and certificates accordingly.

## What you still have to do

Sign processor agreements with your cloud and model providers; run a DPIA for personal data; decide residency
per deployment (data host, agent host, model endpoint); manage certificate issuance and rotation (any ACME,
Vault or mesh CA works, the services read PEM files); set retention to your legal requirement; restrict who may
be granted `SELECT` on raw tables.
