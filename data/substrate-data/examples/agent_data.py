"""Query governed data as an agent or application: `uv run examples/agent_data.py`.

Run `make up && make dev && make dev-compute && make demo` first. The agent authenticates as its own
service principal (OIDC client credentials), sees only the tables its grants allow, and receives
results as Arrow: Polars and pandas read them without copying through JSON.
"""

from __future__ import annotations

import os

from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient

env = {**dotenv_values(".env"), **os.environ}


def main() -> None:
    client = DataClient(
        env.get("SUBSTRATE_DATA_API_URL", "http://127.0.0.1:8020"),
        token=ClientCredentials(
            env["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", env["BENCH_CLIENT_SECRET"]
        ),
    )
    for table in client.tables("main", "nyc"):
        print(f"{table['full_name']}: {table['row_count']:,} rows, {table['file_count']} files")
    monthly = client.sql(
        "SELECT date_trunc('month', tpep_pickup_datetime) AS month, count(*) AS trips, "
        "round(avg(total_amount), 2) AS avg_fare FROM main.nyc.yellow GROUP BY 1 ORDER BY 1"
    )
    print(monthly)  # a polars DataFrame; filters and aggregation ran next to the data


if __name__ == "__main__":
    main()
