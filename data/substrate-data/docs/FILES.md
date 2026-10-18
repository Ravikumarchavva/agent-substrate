# Files

Governed, versioned files in **volumes**. A volume is the existing catalog securable
(`catalog.schema.volume`, privileges `READ_VOLUME` / `WRITE_VOLUME`); the Files API adds paths, versions,
quotas, ownership and audit on top of it. The API never carries file bytes: it signs URLs, clients and
sandboxes send and fetch bytes straight to object storage (ADR 0004 and 0005).

## Model

| Thing | Where | Notes |
| --- | --- | --- |
| Bytes | `volumes` bucket, key `volumes/{volume_id}/objects/{uuid}` | Keys are opaque: no file name, tenant or user ever appears in storage. Separate from the staging and warehouse buckets. |
| `volume_files` | catalog database | One row per `(volume, path)`: the current `size`, `sha256`, `content_type`, `version`, `created_by`, `created_at`, `deleted_at`. |
| `volume_file_versions` | catalog database | Every version of every file (object key, size, hash, author, time) until purged. |
| `volume_quotas` | catalog database | The optional hard byte quota of a volume. |
| `volume_uploads` | catalog database | Unfinished uploads and upload sessions, with their expiry. |

**Only the control plane holds credentials for the volumes bucket.** Compute workers' S3 identity has no
action on it (`scripts/bootstrap.py`; a test tries the worker credentials and is denied), and the agent
host holds no storage credentials at all. A compromised worker cannot read users' files.

## Paths

A path is relative to the volume, in one normal form: NFC-normalised, repeated slashes collapsed, at most
1024 bytes and 255 per segment. Refused with `400 invalid-path`: empty paths, a leading `/`, `.` or `..`
segments, backslashes, NUL and control characters, a trailing `/`. The last segment may not be `versions` or
end in `:restore` / `:download-url`, because the routes below use those words. Prefixes are plain string
prefixes (`docs/rep` matches `docs/report.txt`; `docs/` only matches inside `docs`) and are case-sensitive.

### Ownership: `users/{sub}/**`

When the token is **delegated** (an agent acting for a user: `actor` is set), paths under `users/{sub}/`
belong to that subject. Such a token may read and write `users/{its sub}/**` and anything outside `users/`
that grants allow, and gets `403 path-forbidden` for another subject's folder. Listings and usage under
`users/` show only the caller's own folder; a prefix operation that could span other users (`delete-prefix`
on `users/`) is refused. Ordinary user tokens, metastore admins and members of the `tenant-admins` group are
limited by grants alone.

This is a pluggable `PathPolicy` (`files/policy.py`: `authorize(caller, path, action)` and
`authorize_prefix(caller, prefix, action)`). The default is `OwnerPrefixPolicy`; the later tenancy work
extends it by replacing `FilesService.policy`.

## Routes

`{v}` is `catalog.schema.volume`. Reads need `READ_VOLUME`, mutations `WRITE_VOLUME`. Delegated tokens also
need a scope: reads `files:read` or `files:write`, mutations `files:write`. Every other route declares no
scope and stays closed to them (tested).

| Route | Purpose |
| --- | --- |
| `POST /api/v1/volumes/{v}/files:initiate-upload` | `{path, size, content_type?, sha256?}`. Returns an `upload_id` and either one presigned `PUT` (up to 64 MiB, with the `headers` to send) or S3 multipart parts (`part_size` from `SUBSTRATE_DATA_FILES_PART_BYTES`, each with `offset`, `length`, `url`). Fails fast on quota and on `If-Match` / `If-None-Match`. |
| `POST /api/v1/volumes/{v}/files:complete-upload` | `{upload_id, parts[{part_number, etag}]}`. Finishes multipart, checks the stored size (and SHA-256 where the store kept one) and creates the **new version atomically**. Safe to retry. |
| `POST /api/v1/volumes/{v}/files:sign` | **Batch**, up to 200 `{path, action: get\|put, size?, content_type?, sha256?}`. Each item returns a URL or its own `error`; for `put`, finish with `complete-upload`. For many small files. |
| `GET /api/v1/volumes/{v}/files?prefix=&delimiter=/&page_token=&page_size=` | Files in path order with cursor pagination; with `delimiter=/` also `directories` (folded sub-folders). |
| `GET …/files/{path}` | Metadata and `ETag`. |
| `GET …/files/{path}:download-url?version=&expires_in=` | A presigned GET (current or a given version). |
| `DELETE …/files/{path}` | Soft delete (`If-Match` honoured): hidden from listings, versions kept until purged. |
| `POST …/files:delete-prefix` | `{prefix}` soft-deletes everything under it; returns counts. |
| `POST …/files:copy-prefix` | `{source_prefix, dest_prefix, overwrite}`. Copies a folder inside the volume (branch forks, promotion). The copies share the stored objects, so it is instant and free; they are independent files from then on. One transaction: a quota failure copies nothing. At most 50 000 files per call. |
| `GET …/files/{path}/versions` | Newest first, paginated. |
| `POST …/files/{path}:restore` | `{version?}`: no version undeletes; a version becomes the new current version. |
| `GET …/usage?prefix=` | `files`, `bytes` under the prefix, `volume_bytes`, `retained_bytes` (old versions), `quota_bytes`. |
| `PUT /api/v1/volumes/{v}/quotas` | `{max_bytes}` (`null` removes). Owner or admin; closed to delegated tokens. |
| `POST /api/v1/upload-sessions` | A pending upload outside any volume (`{size, content_type?, sha256?}`), expiring after 24 hours. |
| `POST /api/v1/upload-sessions/{id}:promote` | `{volume, path, parts?}`: finishes the upload and **server-side copies** it into a volume path (grants, path policy, quota and preconditions apply), then drops the session. |
| `POST /api/v1/tenants` | Provision a tenant (below). |

Errors are RFC 9457 (`application/problem+json`). Notable: `invalid-path` 400, `path-forbidden` 403,
`insufficient-scope` 403, `token-read-only` 403, `precondition-failed` 412, `quota-exceeded` 413,
`upload-incomplete` / `upload-size-mismatch` / `checksum-mismatch` 409, `upload-expired` 410,
`storage-unavailable` 503.

### Versions and compare-and-swap

Each upload makes version `n+1` and sets the `ETag` to `"n+1"`. Send `If-None-Match: *` to create only, or
`If-Match: "n"` to replace only the version you read; the check runs inside the committing transaction, so
two writers cannot both win. A deleted path counts as absent. Old versions are kept until the sweeper purges
them (`SUBSTRATE_DATA_FILES_VERSION_RETENTION_DAYS`, default 30; 0 keeps them forever): a version is purged that
long after it was superseded, and a deleted file's versions that long after the delete. An object is removed
from storage only when no remaining version refers to it.

### Quotas

A volume may carry a hard byte quota. It counts **current, undeleted files** (logical bytes, so a copy counts
too); superseded versions are reported as `retained_bytes` and bounded by the purge window. A write that
would pass the quota gets `413 quota-exceeded` (with `used_bytes`, `requested_bytes`, `quota_bytes`),
checked at `initiate-upload` for a fast answer and again inside the commit, where concurrent writers are
serialised on the quota row. The library's `WorkspaceQuotaExceededError` is mapped from it by the client
(`QuotaExceeded`). Replacing a file counts only the difference; exactly at the quota is allowed.

### Integrity

The signed upload pins `Content-Length`, and a declared `sha256` is sent as `x-amz-checksum-sha256`, which
the object store verifies on the single-PUT path (a wrong hash is refused before anything is stored). On
completion the stored size must equal the declared size, and a hash the store kept must equal the declared
one; otherwise the object is deleted. `sha256_verified` tells a reader whether the store checked the hash
(false for multipart, where S3 keeps no whole-object SHA-256, and when none was declared).

## Pending uploads and the sweeper

Every unfinished upload (and upload session) expires `SUBSTRATE_DATA_UPLOAD_SESSION_TTL_HOURS` (24) after it
starts. A background sweeper in each API replica (`SUBSTRATE_DATA_FILES_SWEEP_INTERVAL_SECONDS`, 900) aborts
expired multipart uploads, deletes their objects, purges old versions, and removes the files and objects of
volumes that were unregistered. It is idempotent, so replicas may run it together.

## Audit

Every mutation is written to the hash-chained audit log with **ids only**: the volume (its full name is the
securable, its id is in the detail), the file id, version, size and counts. A file or prefix appears only as
`path_hash` (the first 16 hex digits of the SHA-256 of the path), never by name. The principal and, for an
agent, the acting agent are recorded as for any other event. Signing and download-URL issuance are audited too.

## Tenant provisioning

`POST /api/v1/tenants {tenant_id, alias}` is an idempotent ensure for the platform that onboards tenants. It
creates catalog `tenant_<alias>` (its comment records the tenant id), schemas `workspace` and `knowledge`,
volume `workspace.files`, and grants `USE_CATALOG`, `USE_SCHEMA`, `READ_VOLUME` and `WRITE_VOLUME` to
`group:org:<tenant_id>`; metastore admins own everything created. It returns the names and `created`
(201 the first time, 200 after). The same alias for another tenant id is `409 tenant-conflict`.

Who may call it: a metastore admin, or a member of the group `SUBSTRATE_DATA_PROVISIONER_GROUP` (default
`data-provisioners`), the `data:provisioner` role expressed as a group claim in the access token. Delegated
tokens never can: the route declares no scope.

## Settings

| Variable | Default | |
| --- | --- | --- |
| `SUBSTRATE_DATA_VOLUMES_BUCKET` | `substrate-volumes` | Create it; only the control plane's key may use it. |
| `SUBSTRATE_DATA_VOLUMES_PUBLIC_ENDPOINT_URL` | (the S3 endpoint) | Address clients reach for presigned URLs, when different. |
| `SUBSTRATE_DATA_FILES_PART_BYTES` | 16 MiB | Multipart part size (5 MiB to 5 GiB; at most 10 000 parts). |
| `SUBSTRATE_DATA_FILES_URL_TTL_SECONDS` | 900 | Lifetime of presigned URLs. |
| `SUBSTRATE_DATA_FILES_MAX_FILE_BYTES` | 5 TiB | Largest single file. |
| `SUBSTRATE_DATA_UPLOAD_SESSION_TTL_HOURS` | 24 | Expiry of unfinished uploads and sessions. |
| `SUBSTRATE_DATA_FILES_VERSION_RETENTION_DAYS` | 30 | Purge window for old versions (0 keeps them). |
| `SUBSTRATE_DATA_FILES_SWEEP_INTERVAL_SECONDS` | 900 | Sweeper period. |
| `SUBSTRATE_DATA_PROVISIONER_GROUP` | `data-provisioners` | May call `POST /tenants`. |

The fair-use limiter (`SUBSTRATE_DATA_RATE_LIMIT_PER_MINUTE`, 1200 with a burst of 200) counts every Files call
per principal. A small-file workload (an agent writing hundreds of files) should use `files:sign` plus
parallel direct transfers and may need a higher limit.

## Client

`substrate-data-client` 0.2 has `client.files` (sync) and `AsyncDataClient.files` implementing exactly these
routes, with `upload` (bytes, path or stream; parallel multipart), `read` / `download` / `iter_bytes`,
`put_many` / `get_many`, conditions (`if_none_match=True`, `if_match=info`), listing, versions, restore,
copy / delete prefix, usage and quotas. `FILESTORE_KEY_MAP.md` in the client package shows how the agent
library's `FileStore` keys map onto volumes and paths.

## Not built

- **Cross-volume copy.** `copy-prefix` stays inside one volume (objects are shared per volume so unregistering
  a volume can erase it cleanly).
- **Custom object metadata.** Only `content_type` is stored.
- **Whole-object SHA-256 for multipart uploads.** Recorded when declared, not verified (`sha256_verified:
  false`).
- **Range reads and server-side zip/archive.** Presigned GETs accept `Range` headers directly.
- **Legacy volume routes.** `…/volumes/{name}/files` and `…/access` in `assets.py` still address the staging
  bucket (UI uploads of up to 64 MiB). New code should use this API; the legacy routes and the UI that calls
  them should move over and be deleted.
- **Legal hold.** Erasure and export by subject exist (`POST /api/v1/erasure-requests`, `/exports`; see
  [DOCUMENTS.md](DOCUMENTS.md)); nothing can yet pause them for a hold.
- **Per-prefix quotas.** Usage is reported per prefix; the quota is per volume. Concurrent writers are
  serialised by locking the volume's quota row, which exists only while a quota is set.
- **Orphan object garbage collection** for objects whose version row was never written (a crash between
  storage and catalog); the 24 hour expiry covers unfinished uploads, not that window.
