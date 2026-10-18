# Changelog

## [0.2.0](https://github.com/Ravikumarchavva/agent-substrate/compare/substrate-cloud-v0.1.0...substrate-cloud-v0.2.0) (2026-10-18)


### ⚠ BREAKING CHANGES

* **cloud:** the gateway serves the monolith's /api/v1 contract
* **cloud:** policy, admin and tool executor on the token, the audit trail and the caller's files
* **cloud:** live stream and human gate work from the run journal
* **cloud:** conversation, job controller and agent runtime take identity from the token and finish runs
* **cloud:** shared plumbing for the services (service discovery, token-forwarding client, runtime view, pruned events)
* **cloud:** remove the identity service (Keycloak is the identity service) and tell services where their peers are
* **cloud:** serve the Agent API under /api/v1 with a contract snapshot
* **cloud:** verify Keycloak tokens in the monolith and every microservice

### Features

* add narrowing controls for chat requests with allowed_tools and knowledge_base ([45e765f](https://github.com/Ravikumarchavva/agent-substrate/commit/45e765f409c1b6f24131f5b4142e01f6023460b3))
* add reasoning parameter to agent creation and chat request handling ([1ba270a](https://github.com/Ravikumarchavva/agent-substrate/commit/1ba270a7a0ff4be40962656053b0d164025d6397))
* add usage tracking and reporting by day ([2824b49](https://github.com/Ravikumarchavva/agent-substrate/commit/2824b49c8e14704dd75245d0feed1aad28cf0196))
* **chats:** enhance chat view and message retrieval with additional metadata and improved logic ([6bb9f97](https://github.com/Ravikumarchavva/agent-substrate/commit/6bb9f97b0db4fff6ae601be40872613744405798))
* **cloud:** conversation, job controller and agent runtime take identity from the token and finish runs ([1af4093](https://github.com/Ravikumarchavva/agent-substrate/commit/1af409326afd3be05945131f5ec378258d2dea86))
* **cloud:** documents and memory through the data service when DATA_SERVICE_URL is set; erasure goes data, runtime, redis ([a9f910d](https://github.com/Ravikumarchavva/agent-substrate/commit/a9f910deba38843ce939c8a07a49692114599344))
* **cloud:** files, workspace, GDPR and admin storage go through the data service ([b7566a6](https://github.com/Ravikumarchavva/agent-substrate/commit/b7566a635ea1b7ad08553646bd0f9aa82ff084e7))
* **cloud:** FileStoreProvider seam so a run can be bound to its own object store ([159de29](https://github.com/Ravikumarchavva/agent-substrate/commit/159de29296f92d9571378aa8fe7978068a5e561a))
* **cloud:** harden the per-run data token minter ([465d534](https://github.com/Ravikumarchavva/agent-substrate/commit/465d534506ff7b2a8272addc895ff983fd35212a))
* **cloud:** live stream and human gate work from the run journal ([7c96671](https://github.com/Ravikumarchavva/agent-substrate/commit/7c9667194045fe78bda8ed7681c0ada37470761e))
* **cloud:** per-run data tokens, public key endpoint and sandbox gateway ([13885dd](https://github.com/Ravikumarchavva/agent-substrate/commit/13885dd71a4621cb34cd31f5c9d116d379312ca8))
* **cloud:** per-tenant entitlements replace the daily_message_limit claim ([685bb9c](https://github.com/Ravikumarchavva/agent-substrate/commit/685bb9c354b4360ce82e887ffe3d6f1c168b2da3))
* **cloud:** policy, admin and tool executor on the token, the audit trail and the caller's files ([9ca8474](https://github.com/Ravikumarchavva/agent-substrate/commit/9ca8474cd399579d5a7613a0e03ac4ec98ea5dc7))
* **cloud:** remove the identity service (Keycloak is the identity service) and tell services where their peers are ([df5b085](https://github.com/Ravikumarchavva/agent-substrate/commit/df5b0857036161724c45f5b1faa5ce989e94ff07))
* **cloud:** serve the Agent API under /api/v1 with a contract snapshot ([72c4f4a](https://github.com/Ravikumarchavva/agent-substrate/commit/72c4f4a6f22bd3fd1513d84252acf2ff5fadce74))
* **cloud:** shared plumbing for the services (service discovery, token-forwarding client, runtime view, pruned events) ([c9425d8](https://github.com/Ravikumarchavva/agent-substrate/commit/c9425d8844333c6899fa85e921be40325af14212))
* **cloud:** the gateway serves the monolith's /api/v1 contract ([ce32f99](https://github.com/Ravikumarchavva/agent-substrate/commit/ce32f99d21907c7cc33f029637d4369dabf6c8ef))
* **cloud:** verify Keycloak tokens in the monolith and every microservice ([7052e7f](https://github.com/Ravikumarchavva/agent-substrate/commit/7052e7fcff9b5da15283199c0105ef38a3d8e2d6))
* **data:** DataFileStore, KeyScopedFileStore and DataUploadSessions behind agent-substrate[data] ([495d955](https://github.com/Ravikumarchavva/agent-substrate/commit/495d9551f911b816060507e4329c7a72021b2ee2))
* Enhance trigger routes with tenant isolation and add comprehensive tests for tenant-based access ([5766ffc](https://github.com/Ravikumarchavva/agent-substrate/commit/5766ffcaff71208a62ab7db5c014df1b6bb445a3))
* **groups:** add token cap management and member refresh on updates ([f0bd3dd](https://github.com/Ravikumarchavva/agent-substrate/commit/f0bd3dd11e362cbd7918075f586a6d1e8088018b))
* Implement agent profiles and delegation system ([f072800](https://github.com/Ravikumarchavva/agent-substrate/commit/f0728003d264c95799952fe12f8d4f2652fd1854))
* Implement LLMOrganiser for topic tree management and enhance Library.reorganise functionality ([b1a6257](https://github.com/Ravikumarchavva/agent-substrate/commit/b1a625701e9b4b7700fa39c86b28f18f7ff0621c))
* Implement scheduled task catch-up and trigger persistence ([0234627](https://github.com/Ravikumarchavva/agent-substrate/commit/0234627fc875b6b2413f67fe180a800e77c9cf74))
* **logging:** implement QuietPolls filter to enhance access log readability ([0f8a325](https://github.com/Ravikumarchavva/agent-substrate/commit/0f8a3257adea7ffe76dff5e532963725de951924))


### Bug Fixes

* **ci:** resolve delegation preview assertion, frontmatter roundtrip, and dependency boundaries ([d6be873](https://github.com/Ravikumarchavva/agent-substrate/commit/d6be873e54204f2fa6458b5b781d5e6c9cee45d8))
* **cloud:** require org membership for files tokens; reconcile Track D and E ([7a39a62](https://github.com/Ravikumarchavva/agent-substrate/commit/7a39a62def7ecc2313415179d9e35d88c2a84c8b))
* update uv.lock to add pypdfium2 dependency and clean up optional dependencies ([88341c6](https://github.com/Ravikumarchavva/agent-substrate/commit/88341c6194e41861436e4b7c2a107ca6ea406962))


### Documentation

* update architecture references, store scaling guidance, and dependency metadata ([78b95b0](https://github.com/Ravikumarchavva/agent-substrate/commit/78b95b05b1f80962d22dbd34075b387374a62492))
