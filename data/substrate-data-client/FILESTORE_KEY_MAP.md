# Mapping the agent library's `FileStore` onto volumes

For whoever writes `DataFileStore` (the `agent-substrate[data]` adapter, ADR 0004). It implements the
`FileStore` protocol of `agents/agent-substrate/src/substrate/stores/files.py` with `client.files`
(`AsyncDataClient` in the async adapter). Nothing here is enforced by the client; it is the contract the
adapter and the server agree on.

## Keys

`FileStore` keys are path-shaped strings built by the library's workspace layout:

```
tenants/{tenant}/users/{user}/...                       user files, blobs, knowledge-base documents
tenants/{tenant}/conversations/{conversation}/...       artifacts and uploads of one conversation
```

A key splits into **(volume, path)** at its second segment:

| Part | Rule | Example |
| --- | --- | --- |
| volume | the tenant's workspace volume, `tenant_<alias>.workspace.files`, where `<alias>` is the alias the tenant was provisioned with (`client.tenants.ensure(tenant_id, alias)` returns the volume name) | `tenant_acme.workspace.files` |
| path | everything after `tenants/{tenant}/`, unchanged | `users/u-42/reports/q3.csv` |

So `tenants/acme/users/u-42/reports/q3.csv` is path `users/u-42/reports/q3.csv` in volume
`tenant_acme.workspace.files`, and the `users/{user}/` convention is exactly the server's ownership rule:
**`{user}` must equal the token's `sub`**, or an agent token gets `403 path-forbidden`. The adapter keeps a
`tenant_id -> volume` map (configuration, or `tenants.ensure` at startup) and does not guess the alias.
A key that does not start with `tenants/{tenant}/` is the library's `WorkspacePathError` (raise it before any
call); the server independently refuses `..`, a leading `/`, backslashes and control characters
(`400 invalid-path`, a `DataError` subclass `InvalidRequest`).

## Methods

| `FileStore` | Client call | Notes |
| --- | --- | --- |
| `upload(key, data, content_type=...)` | `files.upload(volume, path, data, content_type=content_type)` | Replaces the current version (a new version is kept). `data: bytes` is one request up to 64 MiB, multipart above. There is no object metadata on the server: `FileStore.upload` has none either, so nothing is lost; do not invent a `metadata` argument. |
| `download(key)` | `files.read(volume, path)` | Raises `NotFound` when absent (the protocol says "raises if absent"). |
| `exists(key)` | `files.exists(volume, path)` | A metadata `GET`, a point check. |
| `delete(key)` | `files.delete(volume, path, missing_ok=True)` | Soft delete; a missing key is a no-op. |
| `list_prefix(prefix)` | `[("tenants/{t}/" + f.path, f.size, f.mtime) for f in files.list(volume, rest_of_prefix)]` | `FileInfo.mtime` is epoch seconds (float), as the protocol wants. The listing is lazy and paginated. |
| `delete_prefix(prefix)` | `files.delete_prefix(volume, rest_of_prefix)["deleted"]` | Returns the count. The server refuses an empty prefix, which is correct: a tenant prefix is never empty. |
| `copy_prefix(src, dst)` | `files.copy_prefix(volume, src_rest, dst_rest)["copied"]` | Both prefixes in the same tenant volume (the library only copies within a tenant). Server-side, one transaction, shares the stored objects. Raises `InvalidRequest` if the prefixes nest. |
| `presign_url(key, expires_in=3600)` | `files.download_url(volume, path, expires_in=expires_in)["url"]` | The server caps `expires_in` at 7 days. |
| `usage_bytes(tenant_id, force=False)` | `files.usage(volume).volume_bytes` | Exact (summed in the catalog), so `force` has nothing to bypass. Counts current, undeleted files only; superseded versions are `retained_bytes`. |

## Quota

The library raises `WorkspaceQuotaExceededError(tenant_id, used_bytes, quota_bytes)`. The server enforces a hard
per-volume quota (set by the owner with `files.set_quota(volume, max_bytes)`) and answers
`413 quota-exceeded`; the client raises `QuotaExceeded` with `used_bytes` and `quota_bytes`:

```python
try:
    await client.files.upload(volume, path, data, content_type=content_type)
except QuotaExceeded as exc:
    raise WorkspaceQuotaExceededError(tenant_id, exc.used_bytes or 0, exc.quota_bytes or 0) from exc
```

The adapter does not need its own quota arithmetic or a pre-check; a failed write stores nothing.

## Errors worth mapping

| Client exception | Meaning for the adapter |
| --- | --- |
| `NotFound` | absent key (`download`), or a volume the token cannot see |
| `Forbidden` (`code == "path-forbidden"`) | a key under another user's `users/{sub}/` |
| `InsufficientScope` | the run's token lacks `files:read` / `files:write`; configuration, not retryable |
| `QuotaExceeded` | see above |
| `Unavailable`, `RateLimited` | already retried (idempotent calls) by the client; surface as "storage unavailable" |

## Many small files

For a workflow that writes or reads hundreds of keys (copying a knowledge base, a code-interpreter run's outputs)
use `files.put_many(volume, {path: bytes})` and `files.get_many(volume, [paths])`: one signing call per 200
files, then parallel direct transfers. They return per-path outcomes instead of raising, so the adapter decides
what a partial failure means.

## Conformance

`agents/agent-substrate/src/substrate/testing/conformance/file_store.py` runs unchanged against the adapter; the
two behaviours to watch are that `list_prefix` returns keys (not paths) and that `delete` of a missing key
returns normally.
