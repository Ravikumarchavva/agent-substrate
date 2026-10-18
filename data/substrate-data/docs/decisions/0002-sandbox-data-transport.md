# 0002 — Sandbox data transport: a bind-mounted Unix socket (spike: Flight; shipped: HTTP + Arrow IPC)

- **Status:** accepted, 2026-10-17. **Amended 2026-10-17:** the socket-in-a-closed-jail mechanism below is what shipped, but the protocol on it is HTTP streaming Arrow IPC, not Flight (see "What shipped").
- **Spike:** `benchmarks/spikes/sandbox_transport/`.

## Decision

agent-substrate's code interpreter reaches substrate-data with **Arrow Flight over gRPC on a Unix socket**. The socket is bind-mounted **read-only** into the nsjail jail at `/run/substrate-data/flight.sock`, next to a per-call token file at `/run/substrate-data/token`.

- The jail's network namespace stays closed. Because it is a path-based Unix socket, it crosses the network namespace while the internet stays unreachable.
- The k8s sandbox runtime uses the same client over TCP, with egress limited to Flight by a NetworkPolicy.

## Spike setup

- **nsjail** 3.6, built exactly as in agent-substrate's `backend.Dockerfile`.
- **Jail flags:** the same as `NsjailRuntime` (`-Mo`, cgroup v2, pids 64, swap 0, chroot with read-only system mounts, new network namespace), with a **1 GiB** memory cap.
- **Server:** a PyArrow 26 Flight server with bearer-token middleware on the host side of the socket.

| Check | Result |
| --- | --- |
| Internet from the jail (`connect 1.1.1.1:443`) | blocked |
| Wrong bearer token | rejected (`FlightUnauthenticatedError`) |
| Result arrives as a Polars DataFrame | yes |
| Small `DoGet` round trip, reused connection, 200 calls | **p50 0.72 ms, p99 0.97 ms** |
| Stream 1.52 GiB under a 1 GiB jail memory cap | completes, 1.16 GiB/s, batch-at-a-time |
| Client cancels an endless stream after 20 batches | server observes cancellation after 22 batches |
| 1 s call timeout against a 5 s handler | `FlightTimedOutError` |

## Consequences

- **Relay only when the Flight endpoint is remote.** No relay is needed when the planner's Flight endpoint runs on the sandbox host. If the endpoint is remote, a host relay maps the Unix socket to TCP with TLS. The relay only copies bytes: it decodes nothing and holds no credentials.
- **Token handling.** The token file is the jail's only credential: a per-call, short-lived token issued by token exchange.
- **The result guard** has to be enforced by the client while it streams, as well as by the server. The spike streamed 1.5 GiB through a 1 GiB jail only because the client processed it batch by batch; collecting it all at once would hit the cgroup limit.
- **Jail user.** The spike's jail ran as UID 0 inside its own namespaces, matching the current runtime flags. Mapping the jail to an unprivileged UID is agent-substrate hardening, tracked separately.

## What shipped (Milestone 2)

The spike proved the *mechanism* (path-based Unix socket crossing a closed network namespace, token as a file,
client-side batch streaming). We then shipped it with the API's existing `POST /api/v1/statements`
(`Accept: application/vnd.apache.arrow.stream`) instead of Flight, because:

- Planning, authorization, pruning and the signed ticket already live on that endpoint; a Flight planner would
  have duplicated them, and the worker hop is already Arrow IPC with no re-encoding.
- One protocol means one set of limits, audit records, cancellation and OpenAPI contract tests.
- The spike's costs that matter (sub-millisecond round trip, streaming under a memory cap) are properties of
  the Unix socket and of batch-at-a-time reading, which the HTTP client keeps.

The data service is assumed to be on **another machine**. The socket exists only on the agent host, as the hop
between the network-less jail and a gateway process in agent-substrate (`DataGateway`); it does not connect two
machines. The gateway makes the single outbound HTTPS connection (verified TLS, optional mutual TLS, pooled), and,
because it understands HTTP, it can set `Host`/SNI correctly for a remote ingress and enforce an allow-list of
read-only paths as the sandbox's only egress. An earlier byte-only relay was replaced for those two reasons. The
per-run token is the file mounted beside the socket. Flight (and Flight SQL for BI tools) remains possible later
behind the same planner; nothing in the client's public API (`sd.sql`, `sd.stream`) would change.

Trust between the machines is by fetched public keys (`jwks_uri`) rather than by shared files; see
[AGENTS.md](../AGENTS.md).
