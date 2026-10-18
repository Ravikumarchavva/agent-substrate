# substrate-data-client

Python client for [Substrate Data](../substrate-data): governed SQL, catalog browsing and versioned files.
**The core depends only on `httpx`.**

```bash
pip install substrate-data-client            # httpx only
pip install 'substrate-data-client[arrow]'   # + Polars/Arrow results (the code-interpreter sandbox image uses this)
pip install 'substrate-data-client[http2]'   # + HTTP/2 multiplexing for the async client (used when installed)
```

```python
import substrate_data_client as sd

df = sd.sql("SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1")  # polars.DataFrame  [arrow]
rows = sd.rows("SELECT zone, count(*) AS trips FROM main.nyc.yellow GROUP BY 1")  # JSON rows, no extra
sd.search("taxi")  # find tables and views you may read
sd.describe("main.nyc.yellow")  # columns, partitioning, row count and a sample
```

Inside a Substrate code-interpreter sandbox the connection is discovered automatically
(`/run/substrate-data`). Elsewhere set `SUBSTRATE_DATA_URL` and `SUBSTRATE_DATA_TOKEN`, or build a client.
`sd.sql`, `sd.arrow` and `sd.stream` raise an `ImportError` that names the extra when it is missing.

## Clients

```python
from substrate_data_client import DataClient, AsyncDataClient, ClientCredentials

client = DataClient("https://data.example.com", token=ClientCredentials(ISSUER, ID, SECRET))

async with AsyncDataClient(url, token=my_async_token_function) as client:   # pooled, HTTP/2 if h2 is installed
    await client.files.upload("main.ws.files", "a.txt", b"hello")
```

`token` may be a string, a function (called for every request), an `async def` function (async client) or
`ClientCredentials`. Idempotent requests (`GET`, `PUT`, `DELETE`, and POSTs the client knows are repeatable)
are retried with jittered backoff on 429, 502, 503, 504 and connection failures, honouring `Retry-After`
(`RetryPolicy`, or `retry=None`). Every request carries a W3C `traceparent` (continue yours with
`with trace_context(header): ...`, or an active OpenTelemetry span).

Errors are RFC 9457 problems, one class each, all `DataError`: `InvalidRequest`, `Unauthorized`, `Forbidden`,
`InsufficientScope` (`.required`), `NotFound`, `Conflict`, `PreconditionFailed`, `QuotaExceeded`
(`.used_bytes`, `.quota_bytes`), `PayloadTooLarge`, `RateLimited` (`.retry_after`), `Unavailable`; each keeps
`status`, `code`, `detail`, `request_id`.

## Namespaces

| | |
| --- | --- |
| `client.sql(q)` | Polars `DataFrame` [arrow]; `.arrow(q)`, `.stream(q)` (record batches, bounded memory) [arrow]; `.rows(q)` JSON, no extra |
| `client.catalog` | `catalogs()`, `schemas()`, `tables()`, `table()`, `views()`, `create_view()`, `volumes()`, `create_volume()`, `search()`, `grants()`, `update_grants()` |
| `client.files` | volumes' files, below |
| `client.tenants` | `ensure(tenant_id, alias)`: idempotent tenant provisioning (needs the provisioner role) |
| `client.upload(table, files)` | import local files into a table (direct to storage, then ingest) |

## Files

```python
files = client.files
V = "tenant_acme.workspace.files"
info = files.upload(V, "users/u1/q3.csv", "q3.csv")                     # path, bytes or file object; multipart > 64 MiB, in parallel
data = files.read(V, "users/u1/q3.csv");  files.download(V, "users/u1/q3.csv", "out.csv")
files.upload(V, "lock", b"me", if_none_match=True)                      # create only  (PreconditionFailed if present)
files.upload(V, "doc", b"v2", if_match=files.info(V, "doc"))            # compare-and-swap
for f in files.list(V, "users/u1/"): print(f.path, f.size, f.version)
files.list_dir(V, "users/u1/")                                          # one folder level: files + sub-folders
list(files.versions(V, "doc")); files.restore(V, "doc", 1)
files.copy_prefix(V, "runs/main/", "runs/branch/"); files.delete_prefix(V, "tmp/")
files.put_many(V, {"a": b"1", "b": b"2"}); files.get_many(V, ["a", "b"])   # batch signing, parallel transfers
files.usage(V, "users/u1/"); files.set_quota(V, 10 * 2**30)
```

The API only signs URLs; this client sends and fetches the bytes itself. Adapters that move bytes some other way
use the low-level calls `initiate_upload`, `complete_upload`, `sign`, `download_url`, `put_to_url`.
`FILESTORE_KEY_MAP.md` explains how the agent library's `FileStore` maps onto these calls. Server side:
[FILES.md](../substrate-data/docs/FILES.md).

## Develop

```bash
uv run --package substrate-data-client pytest data/substrate-data-client/tests
```
