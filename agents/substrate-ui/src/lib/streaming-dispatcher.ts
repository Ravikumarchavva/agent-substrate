import { Agent } from "undici";

// undici's default dispatcher kills a request if no bytes arrive on the
// response body for ~300s — fine for ordinary JSON calls, fatal for any
// backend SSE stream: a long tool call (code_interpreter, a multi-step
// agent loop) can legitimately go quiet between chunks for longer than
// that, and undici doesn't distinguish "slow" from "dead". Found live: a
// real chat send completed successfully end-to-end on the backend (LLM
// call succeeded, logged 200 OK) but the browser still showed "Connection
// error", traced to `TypeError: terminated` / `BodyTimeoutError
// (UND_ERR_BODY_TIMEOUT)` in the proxying route's own fetch. bodyTimeout: 0
// disables that specific watchdog; headersTimeout stays disabled too since
// the same gap can occur before the backend's first byte if a run is
// queued behind others.
//
// Shared by every route that proxies a long-lived backend stream (the
// catch-all /api/backend/[...path] proxy AND /api/chat, which makes its
// own direct fetch to the backend's SSE endpoint rather than going through
// the catch-all) — one dispatcher, so a future stream route doesn't have
// to remember to reimplement this.
//
// package.json pins `undici` to Node's OWN bundled major version
// (check via `node -p process.versions.undici`) — deliberately, not
// incidentally. Node's global fetch() is undici-backed internally, and
// passing a `dispatcher` from a *different* major version's Agent throws
// `InvalidArgumentError: invalid onRequestStart method` (an internal
// interceptor-protocol mismatch) — verified live when this was first
// added with whatever `undici` `pnpm add` resolved to latest (8.x
// against Node 25's bundled 7.x). If this starts throwing that error
// again after a Node upgrade, re-pin to match the new bundled version.
export const streamingDispatcher = new Agent({ bodyTimeout: 0, headersTimeout: 0 });
