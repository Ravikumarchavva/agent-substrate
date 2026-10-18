# Agent sandbox end-to-end (across a host boundary)

Runs the real chain once, by hand: agent code in an nsjail sandbox with no network → `substrate_data_client`
→ a Unix socket (agent host only) → the platform's gateway → **HTTPS with mutual TLS 1.3** → this API (as a
trusted, read-only issuer whose public keys it fetched over HTTPS) → attached DuckDB compute → Arrow back, then
a matplotlib chart and a Polars Excel file in the workspace.

The two sides share no files: `make_pki.py` makes separate `data-side/` and `agent-side/` certificate
directories and only the latter is mounted into the agent container. It checks that plain HTTP and
certificate-less clients are refused, that the run's token only reads what its tenant was granted (the raw
table answers 404), that the internet stays closed, and that the data service fetched the agent's keys.

See `run.sh` for the prerequisites and environment variables. It needs Docker, the sibling agent-substrate
checkout, and the stack from `make up && make dev && make dev-compute && make demo`.
