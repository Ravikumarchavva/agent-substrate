"""Create (or keep) a local demo sign-in: organization `demo`, user `demo` / password `demo`, its data workspace provisioned.

For a laptop cell only; never run it against a cell other people can reach.

    uv run --package loamz-agents python deploy/compose/demo-user.py deploy/compose/.env
"""

import sys
from pathlib import Path

import httpx

from loamz.identity.tenancy import tenant_alias

ENV = dict(
    line.split("=", 1)
    for line in Path(sys.argv[1]).read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
KC = f"http://localhost:{ENV.get('PORT_KEYCLOAK', '8080')}"
HOST = {"Host": ENV.get("KEYCLOAK_PUBLIC_URL", "http://keycloak.localhost:8080").split("://", 1)[1]}
DATA = f"http://localhost:{ENV.get('PORT_DATA_API', '8020')}/api/v1"
NAME = "demo"
http = httpx.Client(timeout=30)

token = http.post(
    f"{KC}/realms/master/protocol/openid-connect/token",
    headers=HOST,
    data={
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": ENV["KEYCLOAK_ADMIN_USER"],
        "password": ENV["KEYCLOAK_ADMIN_PASSWORD"],
    },
).json()["access_token"]
admin = {**HOST, "Authorization": f"Bearer {token}"}
api = f"{KC}/admin/realms/loamz"

orgs = http.get(
    f"{api}/organizations", headers=admin, params={"search": NAME, "exact": "true"}
).json()
if orgs:
    org_id = orgs[0]["id"]
else:
    r = http.post(
        f"{api}/organizations",
        headers=admin,
        json={"name": NAME, "alias": NAME, "domains": [{"name": "demo.localhost"}]},
    )
    r.raise_for_status()
    org_id = r.headers["Location"].rsplit("/", 1)[1]

users = http.get(f"{api}/users", headers=admin, params={"username": NAME, "exact": "true"}).json()
if users:
    user_id = users[0]["id"]
else:
    r = http.post(
        f"{api}/users",
        headers=admin,
        json={
            "username": NAME,
            "email": "demo@demo.localhost",
            "emailVerified": True,
            "enabled": True,
            "firstName": "Demo",
            "lastName": "User",
        },
    )
    r.raise_for_status()
    user_id = r.headers["Location"].rsplit("/", 1)[1]
http.put(
    f"{api}/users/{user_id}/reset-password",
    headers=admin,
    json={"type": "password", "value": NAME, "temporary": False},
).raise_for_status()
r = http.post(
    f"{api}/organizations/{org_id}/members",
    headers={**admin, "Content-Type": "application/json"},
    content=f'"{user_id}"',
)
if r.status_code not in (201, 204, 409):
    r.raise_for_status()

cell = http.post(
    f"{KC}/realms/loamz/protocol/openid-connect/token",
    headers=HOST,
    data={
        "grant_type": "client_credentials",
        "client_id": "cell-admin",
        "client_secret": ENV["CELL_ADMIN_CLIENT_SECRET"],
    },
).json()["access_token"]
r = http.post(
    f"{DATA}/tenants",
    headers={"Authorization": f"Bearer {cell}"},
    json={"tenant_id": org_id, "alias": tenant_alias(org_id)},
)
r.raise_for_status()
print(f"Sign in at {ENV.get('SUBSTRATE_UI_URL', 'http://localhost:3000')}/chat as {NAME} / {NAME}")
