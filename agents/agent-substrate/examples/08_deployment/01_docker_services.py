from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()

"""Example 08-1: Docker services health check for agent-substrate.

Verifies that the required Docker services are reachable before starting the engine.
Run this script after `make infra-up` to confirm all services are UP.

Services checked:
  - PostgreSQL  localhost:5432
  - Redis       localhost:6379
  - MCP server  localhost:3000
"""

import asyncio
import socket
import sys
# Infrastructure: requires `make infra-up` in agent-substrate/
#   docker compose -f docker/docker-compose.yml up -d

SERVICES = [
    ("postgres", "localhost", 5432),
    ("redis", "localhost", 6379),
    ("mcp-server", "localhost", 3000),
]

# ---


def check_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---


async def section_1_tcp_checks() -> list[str]:
    """Section 1 — TCP port checks for each service."""
    print("=== Section 1: Service TCP health checks ===")

    down: list[str] = []
    for name, host, port in SERVICES:
        up = check_tcp(host, port)
        status = "UP  ✓" if up else "DOWN ✗"
        print(f"  {status}  {name:12s}  {host}:{port}")
        if not up:
            down.append(name)

    return down


# ---


async def section_2_memory_clients() -> None:
    """Section 2 — Import and connect RedisMemory and PostgresMemory."""
    print("\n=== Section 2: Memory client connectivity ===")

    # --- RedisMemory ---
    try:
        from substrate.capabilities.history import RedisHistoryProvider

        mem = RedisMemory(
            session_id="healthcheck",
            redis_url=f"redis://{settings.REDIS_HOST}:6379/0"
            if hasattr(settings, "REDIS_HOST")
            else "redis://localhost:6379/0",
            default_ttl=10,
        )
        # Ping via raw redis client
        import redis.asyncio as aioredis

        r = aioredis.from_url("redis://localhost:6379/0")
        pong = await r.ping()
        await r.aclose()
        print(f"  ✓  RedisMemory  — ping={pong}")
    except Exception as exc:
        print(f"  ✗  RedisMemory  — {exc}")

    # --- PostgresMemory ---
    try:
        from substrate.capabilities.history import PostgresHistoryProvider

        db_url = (
            settings.DATABASE_URL
            if hasattr(settings, "DATABASE_URL") and settings.DATABASE_URL
            else "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
        )
        pg = PostgresMemory(database_url=db_url)
        await pg.connect()
        await pg.disconnect()
        print(f"  ✓  PostgresMemory — connected to {db_url.split('@')[-1]}")
    except Exception as exc:
        print(f"  ✗  PostgresMemory — {exc}")


# ---


async def section_3_summary(down: list[str]) -> None:
    """Section 3 — Print summary and remediation command."""
    print("\n=== Section 3: Summary ===")

    total = len(SERVICES)
    up_count = total - len(down)
    print(f"  {up_count}/{total} services reachable")

    if not down:
        print("  All services UP — agent-substrate is ready to start.")
        print("  Run: cd agent-substrate && uv run start")
    else:
        print(f"  Services DOWN: {down}")
        print("\n  To start all services:")
        print("    cd agent-substrate")
        print("    docker compose -f docker/docker-compose.yml up -d")
        print("\n  Or start specific services only:")
        svc_args = " ".join(
            s for name, _, _ in SERVICES if name in down for s in [name]
        )
        print(f"    docker compose -f docker/docker-compose.yml up -d {svc_args}")


# ---


async def main() -> None:
    down = await section_1_tcp_checks()
    await section_2_memory_clients()
    await section_3_summary(down)

    if down:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
