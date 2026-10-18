# agent-substrate monorepo

Layout and decisions: [README.md](README.md) and [docs/adr](docs/adr) (0003 to 0009 are the structure of the system).

- Each package has its own guide: `agents/agent-substrate/CLAUDE.md` (the library; read `docs/claude_docs/` there before touching the core), `data/substrate-data/README.md` + `docs/`, `agents/substrate-ui/`.
- Python: one uv workspace at the root (`uv sync --all-packages`, `uv run --package <name> ...`); `data/document-intelligence` is outside it (Python 3.13). Use `uv`, never `pip`. Ruff version is the one pinned in `uv.lock`.
- JavaScript: one pnpm workspace at the root.
- Boundaries: the agent library depends on nothing in `data/`; adapters to substrate-data live behind the `agent-substrate[data]` extra ([ADR 0004](docs/adr/0004-agent-runtime-versus-governed-data.md)). The private control plane never imports source from here, only released artifacts.
- Never commit secrets: `.env*`, `*.pem`, `.workspace/` are ignored. Pushes and releases are done by the owner.
- No backward compatibility is needed (nothing is deployed): delete old code instead of shimming; APIs are versioned (`/api/v1`) and errors are RFC 9457.
