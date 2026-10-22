# loamz-data

Typed Python client for the Loamz Data API: governed SQL, catalog, files, documents, memory and privacy, sync and
asyncio. **The core depends only on `httpx` and `loamz-identity`** (for the tenancy rules).

```bash
pip install loamz-data            # core
pip install 'loamz-data[arrow]'   # + Polars/Arrow results (the code-interpreter sandbox image uses this)
pip install 'loamz-data[http2]'   # + HTTP/2 multiplexing for the async client
```

```python
import loamz.data as ld

df = ld.sql(
    "SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1"
)  # polars.DataFrame  [arrow]
rows = ld.rows(
    "SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1"
)  # JSON rows, no extra
ld.search("taxi")
ld.describe("main.nyc.yellow")
```

Inside a Loamz code-interpreter sandbox the connection is discovered (`/run/loamz-data`). Elsewhere set
`LOAMZ_DATA_URL` and `LOAMZ_DATA_TOKEN`, or build a client:

```python
from loamz.data import AsyncDataClient, ClientCredentials, DataClient

client = DataClient("https://data.example.com", token=ClientCredentials(ISSUER, ID, SECRET))
async with AsyncDataClient(url, token=my_async_token_function) as client:
    await client.files.upload("tenant_acme.workspace.files", "a.txt", b"hello")
```

Idempotent requests are retried with jittered backoff on 429, 502, 503, 504 and connection failures, honouring
`Retry-After` (`RetryPolicy`, or `retry=None`). Every request carries a W3C `traceparent`.

## Errors

RFC 9457 problems, one class per problem `code`, named like the server's typed errors (`NotFoundError`,
`ForbiddenError`, `InsufficientScopeError`, `ConflictError`, `PreconditionFailedError`, `QuotaExceededError`,
`RateLimitedError`, `ServiceUnavailableError`, …), all `DataError`, each with `status`, `code`, `detail` and
`request_id`.

## Namespaces

| | |
| --- | --- |
| `client.sql(q)` | Polars `DataFrame` [arrow]; `.arrow(q)`, `.stream(q)` [arrow]; `.rows(q)` JSON |
| `client.catalog` | catalogs, schemas, tables, views, volumes, search, grants |
| `client.files` | volumes' files: upload/read/list/versions/restore/copy/delete, batch put/get, usage and quotas |
| `client.documents` | collections, add, search, outline, read, enrichment |
| `client.memory` | memory records: save, get, query, touch, delete, erase |
| `client.erasure`, `client.exports` | one person's (or tenant's) data, removed or handed over |
| `client.tenants` | `ensure(tenant_id)`: idempotent provisioning (the `provision-tenant` role) |

An agent's file keys (`tenants/<tenant>/<path>`) live in the tenant's workspace volume; `locate(key)` returns
`(volume, path)` from `loamz.core.tenancy`, so an adapter never guesses a volume name.

## Develop

```bash
uv run --package loamz-data pytest sdk/python/data/tests -n 4
```
