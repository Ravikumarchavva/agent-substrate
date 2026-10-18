"""Run an agent-style analysis through the real tool, runtime, gateway and a remote data service.

The agent host (this container) shares no filesystem with the data service host: it verifies the data
service against a CA, authenticates to it with a client certificate, and publishes its token-signing
public keys over HTTPS for the data service to fetch.

Usage (inside the container started by run.sh):
    harness.py E2E_DIR SANDBOX_PYTHON DATA_SERVICE_URL AGENT_SIDE_DIR KEYS_PORT
"""

import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.nsjail import (
    NsjailRuntime,
)
from substrate.integrations.tools.code_interpreter.code_interpreter.tool import CodeInterpreterTool
from substrate.types import RunScope
from substrate_cloud.data_access import build_data_access
from substrate_cloud.monolith.routes.data_keys import router as data_keys_router

E2E = Path(sys.argv[1])
PYTHON = sys.argv[2]
SIDE = Path(sys.argv[4])
KEYS_PORT = int(sys.argv[5])
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

AGENT_CODE = r"""
import substrate_data_client as sd
import matplotlib.pyplot as plt

print("== what can I read?")
for hit in sd.search("revenue"):
    print(" ", hit["full_name"], hit["kind"], hit["columns"])

print("== the raw table is not mine to read")
try:
    sd.sql("SELECT count(*) FROM main.nyc.yellow")
except sd.DataError as exc:
    print("  blocked:", exc.status, exc.code)

print("== analysis")
df = sd.sql("SELECT * FROM main.nyc.monthly_revenue ORDER BY month")
print(df)
df = df.with_columns((df["revenue"] / 1e6).round(1).alias("revenue_m"))

fig, ax = plt.subplots(figsize=(9, 4.5))
ax.bar(df["month"].dt.strftime("%b %Y"), df["revenue_m"], color="#7c3aed")
ax.set_title("NYC yellow taxi revenue by month (USD millions)")
ax.set_ylabel("USD millions")
plt.xticks(rotation=45, ha="right")
plt.tight_layout()
plt.savefig("monthly_revenue.png", dpi=110)

zones = sd.sql("SELECT * FROM main.nyc.busiest_zones_august LIMIT 15")
df.write_excel("taxi_report.xlsx", worksheet="Monthly revenue")
print("== saved monthly_revenue.png and taxi_report.xlsx; busiest zone:", zones.row(0))

import socket
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3); print("internet: OPEN")
except OSError:
    print("internet: closed")
"""


class QuietServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


def serve_public_keys(data) -> tuple[QuietServer, list[str], asyncio.Task]:
    """What the agent platform exposes publicly: only the keys that verify its data tokens."""
    app = FastAPI()
    app.include_router(data_keys_router)
    app.state.data_access = data
    fetched: list[str] = []

    @app.middleware("http")
    async def record(request: Request, call_next):
        fetched.append(request.url.path)
        return await call_next(request)

    server = QuietServer(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=KEYS_PORT,
            ssl_certfile=str(SIDE / "keys.crt"),
            ssl_keyfile=str(SIDE / "keys.key"),
            log_level="warning",
        )
    )
    return server, fetched, asyncio.create_task(server.serve())


class Ctx:
    scope = RunScope(
        tenant_id="acme", user_id="agent-user-1", thread_id="e2e-thread", agent_id="analyst"
    )


async def main() -> None:
    runtime = NsjailRuntime(E2E / "scratch", python_bin=PYTHON)
    runtime.preflight()
    data = build_data_access(
        service_url=sys.argv[3],
        data_dir=str(E2E / "data"),
        key_file="",
        relay_dir="/var/tmp/e2e-gateway",
        issuer="agent-substrate",
        audience="substrate-data",
        python_bin=PYTHON,
        ca_file=str(SIDE / "ca.pem"),
        client_cert_file=str(SIDE / "gateway.crt"),
        client_key_file=str(SIDE / "gateway.key"),
    )
    assert data is not None, "data access was not enabled"
    keys_server, keys_fetched, keys_task = serve_public_keys(data)
    tool = CodeInterpreterTool(runtime, data=data)
    print("model is told about data:", "substrate_data_client" in tool.description)
    result = await tool.execute(ctx=Ctx(), code=AGENT_CODE, timeout=120)
    body = (
        json.loads(result.content[0].text)
        if result.content and hasattr(result.content[0], "text")
        else {}
    )
    output = body.get("output") or body.get("stdout") or ""
    print(output)
    files = {f["name"] for f in body.get("output_files", [])}
    outside = (E2E / "scratch" / "e2e-thread" / "main").glob("*")
    produced = {p.name for p in outside if p.is_file()}
    checks = {
        "the run succeeded": body.get("success") is True and not result.is_error,
        "the agent found the granted view": "main.nyc.monthly_revenue" in output,
        "the raw table was refused (404)": "blocked: 404 not-found" in output,
        "the analysis returned rows": "shape: (12, 4)" in output,
        "the internet stayed closed": "internet: closed" in output,
        "the data service fetched our public keys over HTTPS": bool(keys_fetched),
        "a chart and an Excel file were written": {"monthly_revenue.png", "taxi_report.xlsx"}
        <= produced
        and "taxi_report.xlsx" in files,
    }
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    await tool.stop()
    keys_server.should_exit = True
    await keys_task
    if not all(checks.values()):
        raise SystemExit(1)


asyncio.run(main())
