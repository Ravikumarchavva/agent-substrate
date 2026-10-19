# Port registry

One cell, one table: no two components share a port, in compose, in Kubernetes, or when run side by side on a
developer machine. Change a number here first, then in `deploy/compose/.env.example`, the chart's `values.yaml`
and the Dockerfile `EXPOSE`/entrypoint defaults.

"Published" means bound on the Docker host (always on `BIND_ADDR`, default `127.0.0.1`). Everything else is reachable only
on the cell's private network.

| Port | Component | Compose | Notes |
| --- | --- | --- | --- |
| 3000 | agents-console (agents UI) | published (`PORT_UI`) | served under `/chat` |
| 3011 | data-workspace (data workspace UI) | published (`PORT_DATA_WEB`) | |
| 5432 | PostgreSQL | internal | TLS on; databases `agentdb`, `keycloak`, `loamz_data`, `loamz_ui` |
| 6379 | Redis | internal | password required |
| 8000 | agent server, monolith | published (`PORT_AGENT`) | in microservices mode `PORT_AGENT` maps to the gateway (8001) |
| 8001 | agent gateway (microservices) | published as `PORT_AGENT` | the only microservice with a public port |
| 8010 | (retired) agent identity | | removed: Keycloak is the identity service (ADR 0007, 0009); do not reuse for a new component |
| 8011 | agent policy | internal | |
| 8012 | agent conversation | internal | |
| 8013 | agent job controller | internal | |
| 8014 | agent runtime | internal | |
| 8015 | agent tool executor | internal | |
| 8016 | agent human gate | internal | |
| 8017 | agent live stream | internal | |
| 8019 | agent admin | internal | |
| 8020 | loamz-data control plane API | published (`PORT_DATA_API`) | `/api/v1`, `/readyz` |
| 8031 | llama-embed (profile `ml`) | internal | llama.cpp embedding sidecar |
| 8032 | llama-rerank (profile `ml`) | internal | llama.cpp reranker sidecar |
| 8040 | loamz-data controller | internal | the loamz-data default is 8014, which would clash with the agent runtime |
| 8041 | loamz-data worker, `sql` | internal | the default 8011 would clash with agent policy |
| 8042 | loamz-data worker, `jobs` | internal | |
| 8043 | loamz-data worker, `documents` | internal | |
| 8044 | loamz-data worker, `inference` | reserved | not deployed by default |
| 8050 | loamz-data worker `inference` (profile `ml`) | internal | image default is 8080 |
| 8060 | loamz-data-reader | reserved | image default is 8080; start with `--port 8060` |
| 8081 | Keycloak | published (`PORT_KEYCLOAK`) | management/health on 9000 (internal) |
| 8181 | SeaweedFS Iceberg REST catalog | internal | |
| 8333 | SeaweedFS S3 API | published (`PORT_S3`) | browsers use presigned URLs against it |
| 9000 | Keycloak management | internal | `/health/ready` |
| 9333 | SeaweedFS master | internal | used by the bucket-creation job |

In microservices mode the services find each other by `SERVICE_URL_TEMPLATE` (`{name}` is the service in dashes, `{port}` its port from the
table); compose sets `http://agent-{name}:{port}` and the chart `http://<release>-agent-{name}:{port}`. `<NAME>_SERVICE_URL`
(for example `AGENT_RUNTIME_SERVICE_URL`) overrides one peer.
