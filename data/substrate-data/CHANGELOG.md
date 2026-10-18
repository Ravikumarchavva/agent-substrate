# Changelog

## [0.1.2](https://github.com/Ravikumarchavva/agent-substrate/compare/substrate-data-v0.1.1...substrate-data-v0.1.2) (2026-10-18)


### Bug Fixes

* **cell:** wire the agent server to substrate-data end to end ([5839a69](https://github.com/Ravikumarchavva/agent-substrate/commit/5839a693701a14673e6927b91798ddc658afe452))

## [0.1.1](https://github.com/Ravikumarchavva/agent-substrate/compare/substrate-data-v0.1.0...substrate-data-v0.1.1) (2026-10-18)


### Features

* **agent-substrate:** DataLibrary and DataMemoryStore over substrate-data, held to the DocumentLibrary and MemoryStore conformance suites; memory ids travel in bodies ([fec2748](https://github.com/Ravikumarchavva/agent-substrate/commit/fec274850fbdbb7641ec8df18db439a00b4aeac5))
* **api:** /api/v1 routes, publisher, limits, OpenAPI contract ([3df6538](https://github.com/Ravikumarchavva/agent-substrate/commit/3df65382b135ce8f90ff139b005a18a6d483121e))
* **auth:** OIDC verification, trusted issuers, principals, signed query tickets ([207abd3](https://github.com/Ravikumarchavva/agent-substrate/commit/207abd332f262a8038af72211396aed98e3e2ca9))
* **catalog:** securables, grants, policies, usage, hash-chained audit; SQLite and PostgreSQL backends ([cce2bf6](https://github.com/Ravikumarchavva/agent-substrate/commit/cce2bf629fccbd178601970d9becf5cd8cac043b))
* **client:** httpx-only core, async client, typed errors, retries, files namespace ([64def40](https://github.com/Ravikumarchavva/agent-substrate/commit/64def404fea5e3984f74411e053f1bdd782f1fbe))
* **compute:** attach-only controller (HA on PostgreSQL), workers, batch jobs with memory ceilings ([80e9085](https://github.com/Ravikumarchavva/agent-substrate/commit/80e9085c889ad748524b43770369af0ac166f473))
* **core:** settings, errors, HTTP conventions, internal TLS, CLI ([26968ed](https://github.com/Ravikumarchavva/agent-substrate/commit/26968ed026c1e557fa9ab0cac16d628e0cb23371))
* **data-client:** documents, memory, erasure and exports namespaces (sync and async); regenerate the data contract ([76e7ba4](https://github.com/Ravikumarchavva/agent-substrate/commit/76e7ba4967e8222ada4aa7eb21e4e53d979cfd73))
* **data:** Documents, Memory, erasure and export domains running the Library and Memory engines behind governance ([551ebc9](https://github.com/Ravikumarchavva/agent-substrate/commit/551ebc9c395e4e2e55341057affd75f4c3d3504f))
* **data:** scope contract for delegated tokens (require_scope) and restricted-scope enforcement ([d3a5b5a](https://github.com/Ravikumarchavva/agent-substrate/commit/d3a5b5ae2dea30492f97ee8805549d5fe1871c8c))
* **files:** versioned governed files in volumes with quotas, ownership and direct transfers ([13ae81a](https://github.com/Ravikumarchavva/agent-substrate/commit/13ae81adc2c6a38c0aa4d8f127548abf27e98efe))
* **identity:** substrate-identity token verification library ([7164c7a](https://github.com/Ravikumarchavva/agent-substrate/commit/7164c7a8cea18bb8b04354bed8ad93adebd49fb2))
* **sql:** one-pass analysis, planner with views and row/column policies, policy language ([1b9cc19](https://github.com/Ravikumarchavva/agent-substrate/commit/1b9cc1971445ba5ed916f2cc7677483a1379b2fa))
* **tables:** Iceberg service, presigned staging, STS credential vending ([b06e5e5](https://github.com/Ravikumarchavva/agent-substrate/commit/b06e5e58409cceaf7074abc5a68e2ae2cf3eaca3))
* **tenants:** idempotent tenant provisioning (POST /api/v1/tenants) ([f02bec2](https://github.com/Ravikumarchavva/agent-substrate/commit/f02bec226bcf8f255501dfbd555fc88a5010a739))


### Bug Fixes

* **client:** do not wait out a spent quota; test limits without client retries ([ef47207](https://github.com/Ravikumarchavva/agent-substrate/commit/ef47207bd7d57562f93c9abb3c0aa9ed56e29625))


### Performance Improvements

* latency, engine, maintenance and ingest benchmarks; agent example ([f2371a2](https://github.com/Ravikumarchavva/agent-substrate/commit/f2371a2266a8fec7191a237183b678e90e567c0c))


### Documentation

* architecture, operations, security, compliance, compute, agent access and decisions ([186b06a](https://github.com/Ravikumarchavva/agent-substrate/commit/186b06aac64150e0bc82073cc0c53100b899abe4))
* **data:** Documents, memory, erasure and export; deploy the data_retrieval database and settings ([2605653](https://github.com/Ravikumarchavva/agent-substrate/commit/260565346868a3bc5d563c912208ad9194d49c38))
* Files guide and links from README, architecture, operations, security and agent access ([e84f7db](https://github.com/Ravikumarchavva/agent-substrate/commit/e84f7db91afa33158341b8d1d940e648c7a35faa))
* **identity:** usage, token format and non-goals; security note ([16fa2cf](https://github.com/Ravikumarchavva/agent-substrate/commit/16fa2cfcfe9a31de1962c1aa4b5847881677fe43))
* monorepo README, root Makefile, agent guide, gitignore and ADRs 0003-0009 ([3bf3923](https://github.com/Ravikumarchavva/agent-substrate/commit/3bf3923a1d581dcedc625f7f21e34f0844bd5404))
