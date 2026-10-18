"""Let every signed-in user use the locally attached development compute (admin action via API)."""

from __future__ import annotations

import os

from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient

ENV = {**dotenv_values(".env"), **os.environ}

client = DataClient(
    ENV.get("SUBSTRATE_DATA_API_URL", "http://127.0.0.1:8020"),
    token=ClientCredentials(
        ENV["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", ENV["BENCH_CLIENT_SECRET"]
    ),
)
for resource in client.request("GET", "/compute/resources")["resources"]:
    client.request(
        "PATCH",
        f"/grants/compute/{resource['id']}",
        json={"changes": [{"principal": "group:account users", "add": ["USE_COMPUTE"]}]},
    )
    print(f"USE_COMPUTE on {resource['name']} granted to all signed-in users")
