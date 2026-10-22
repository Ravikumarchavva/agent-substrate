# loamz-data

Typed Python client for the Loamz Data API: governed SQL, catalog, files, documents, memory and privacy, sync and
asyncio. **The core depends only on `httpx`, `loamz-core` and `loamz-identity`.** Clients follow the Azure SDK shape: `DataClient(endpoint, credential)`.

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
`LOAMZ_ENDPOINT` and a credential (`LOAMZ_TOKEN`, or `LOAMZ_CLIENT_ID`, `LOAMZ_CLIENT_SECRET`, `LOAMZ_ISSUER`; or a
`~/.loamz/config` profile), or build a client:

```python
from loamz.data import DataClient
from loamz.data.aio import DataClient as AsyncDataClient
from loamz.identity import ClientSecretCredential, DefaultLoamzCredential

client = DataClient("https://data.example.com", ClientSecretCredential(ISSUER, ID, SECRET))
client = DataClient("https://data.example.com", DefaultLoamzCredential())
async with AsyncDataClient(endpoint, credential) as client:
    await client.files.upload("tenant_acme.workspace.files", "a.txt", b"hello")
```

Idempotent requests are retried with jittered backoff on 429, 502, 503, 504 and connection failures, honouring
`Retry-After` (`RetryPolicy`, or `retry=None`). Every request carries a W3C `traceparent`.

## Errors

RFC 9457 problems, one class per problem `code`, named like the server's typed errors (`NotFoundError`,
`ForbiddenError`, `InsufficientScopeError`, `ConflictError`, `PreconditionFailedError`, `QuotaExceededError`,
`RateLimitedError`, `ServiceUnavailableError`, …), all `HttpResponseError` (`loamz.core.exceptions`, with Azure-style aliases such as `ResourceNotFoundError`), each with `status`, `code`, `detail` and
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

## Develop

```bash
uv run --package loamz-data pytest sdk/python/data/tests -n 4
```
