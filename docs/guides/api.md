# API conventions (Loamz Agents and Loamz Data)

Both servers follow the same rules, adapted from Google's API Improvement Proposals. A new endpoint that breaks one
needs a reason in its pull request.

## Resources and URLs
- Everything is under `/api/v1`. A breaking change ships as `/api/v2` beside v1, never in place.
- Resource-oriented nouns in the plural: `/threads/{thread_id}/messages`. Standard methods map to HTTP:
  list `GET /things`, get `GET /things/{id}`, create `POST /things`, replace `PUT /things/{id}`, update
  `PATCH /things/{id}`, delete `DELETE /things/{id}`.
- Anything else is a custom verb with a colon: `POST /threads/{id}:cancel`, `POST /volumes/{v}/files:sign`.
- The caller's own resources live under `/me`: `/api/v1/me/preferences`.
- Ids are opaque strings (UUIDv7 when we mint them). Timestamps are UTC ISO-8601 (`2026-10-18T16:33:00Z`).

## Lists
- Cursor pagination: request `page_size` (default 50, max 200) and `page_token`; response
  `{"items": [...], "next_page_token": "..."}` (absent on the last page). Never offset pagination.
- Filters are named query parameters (`?status=open`), ordering is `order_by=created_at desc`.

## Who may call what
- Every route needs a verified caller unless it is listed as public (`/health`, `/.well-known/*`, shared links).
- Tenant and user come from the token, never from the body or the path of another tenant.
- Another tenant's or another user's private resource is `404`, not `403`: existence is not disclosed.
- Delegated (agent) tokens may only call routes that declare one of their scopes (`loamz.identity.tenancy.SCOPES`).

## Errors
- Domain code raises `loamz.data.storage.errors.*`; the server turns them into RFC 9457
  `application/problem+json` through one table (`HTTP_STATUS`). `type` is `https://loamz.com/problems/<code>`.
- Validation failures are `422 invalid-request` with `errors`. Unexpected errors are `500 internal-error`
  with no internals in the body (they are logged with the request id).
- Clients map `code` back to the same error classes.

## Contracts
- The OpenAPI document of each server is generated and committed under `contracts/<product>/v1/openapi.json`;
  a test fails on drift. TypeScript types for the UIs are generated from it.
