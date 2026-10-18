# Changelog

## [0.2.0](https://github.com/Ravikumarchavva/agent-substrate/compare/substrate-data-client-v0.1.0...substrate-data-client-v0.2.0) (2026-10-18)


### Features

* **agent-substrate:** DataLibrary and DataMemoryStore over substrate-data, held to the DocumentLibrary and MemoryStore conformance suites; memory ids travel in bodies ([fec2748](https://github.com/Ravikumarchavva/agent-substrate/commit/fec274850fbdbb7641ec8df18db439a00b4aeac5))
* **client:** httpx-only core, async client, typed errors, retries, files namespace ([64def40](https://github.com/Ravikumarchavva/agent-substrate/commit/64def404fea5e3984f74411e053f1bdd782f1fbe))
* **client:** substrate-data-client with Arrow streaming and sandbox auto-connect ([c2e8c22](https://github.com/Ravikumarchavva/agent-substrate/commit/c2e8c22379b562d191b61a7226c5e79cd18c0d0f))
* **data-client:** documents, memory, erasure and exports namespaces (sync and async); regenerate the data contract ([76e7ba4](https://github.com/Ravikumarchavva/agent-substrate/commit/76e7ba4967e8222ada4aa7eb21e4e53d979cfd73))
* **data:** Documents, Memory, erasure and export domains running the Library and Memory engines behind governance ([551ebc9](https://github.com/Ravikumarchavva/agent-substrate/commit/551ebc9c395e4e2e55341057affd75f4c3d3504f))


### Bug Fixes

* **client:** do not wait out a spent quota; test limits without client retries ([ef47207](https://github.com/Ravikumarchavva/agent-substrate/commit/ef47207bd7d57562f93c9abb3c0aa9ed56e29625))


### Documentation

* monorepo README, root Makefile, agent guide, gitignore and ADRs 0003-0009 ([3bf3923](https://github.com/Ravikumarchavva/agent-substrate/commit/3bf3923a1d581dcedc625f7f21e34f0844bd5404))
