# loamz-server

Loamz Data: governed tables (Iceberg), SQL, files, documents, memory and tenants for people and agents. One
codebase, run by role:

```bash
loamz-data serve [--roles catalog,sql,tables,compute,files,documents,memory,privacy,tenants]   # the API
loamz-data controller                                     # the compute controller
loamz-data worker --role sql|jobs|documents               # attached compute
loamz-data worker --role inference                        # embedding and reranking (OpenAI / rerank wires)
```

Configuration is one typed tree, `LOAMZ_DATA_*` (`kernel/settings.py`; nested sections use `__`, e.g.
`LOAMZ_DATA_INFERENCE__MODE=local`). The API is `/api/v1`, errors are RFC 9457 problem documents
(`docs/guides/api.md`), and the contract is `specs/data/v1/openapi.json` (drift-tested).

Layout: `kernel/` (settings, container, typed errors and problems, auth on `loamz.server.identity`, scopes, tickets, the
metadata store) and `features/<name>/` (catalog, sql, tables, compute, files, documents, memory, privacy, tenants,
inference). Documents and memory run on `loamz-engine`.

```bash
uv run --package loamz-server pytest server/server/tests -n 8                 # fast tier
uv run --package loamz-server pytest server/server/tests -m integration -n 2  # needs Docker (SeaweedFS)
```
