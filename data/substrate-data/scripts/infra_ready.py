"""Wait for development infrastructure and create the storage it needs. Idempotent."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "deployment/infra.compose.yml"]


def env() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def wait(name: str, url: str, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2, trust_env=False).status_code < 500:
                print(f"{name} ready")
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"{name} did not become ready at {url}")


def weed_shell(commands: str) -> str:
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "seaweedfs", "weed", "shell"],
        input=commands + "\nexit\n",
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    return result.stdout + result.stderr


def main() -> None:
    values = env()
    wait("S3", values["SUBSTRATE_DATA_S3_ENDPOINT_URL"])
    wait("Iceberg catalog", values["SUBSTRATE_DATA_ICEBERG_CATALOG_URL"] + "/v1/config")
    wait(
        "Identity provider",
        values["SUBSTRATE_DATA_OIDC_ISSUER"] + "/.well-known/openid-configuration",
    )
    output = weed_shell(
        f"s3.bucket.create -name {values['SUBSTRATE_DATA_STAGING_BUCKET']}\n"
        f"s3tables.bucket -create -name {values['SUBSTRATE_DATA_WAREHOUSE']} -account 000000000000"
    )
    if "error" in output.lower() and "exist" not in output.lower():
        raise SystemExit("Bucket creation failed:\n" + output[-2000:])
    print("Staging bucket and warehouse table bucket ready")


if __name__ == "__main__":
    main()
