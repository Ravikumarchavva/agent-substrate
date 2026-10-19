"""End-to-end check of a local compose cell: Keycloak org + user, tenant provisioning, agent API, data API.

Run after `docker compose --profile monolith up -d`:  uv run --package loamz-agents python deploy/compose/e2e.py deploy/compose/.env
"""

import sys
import time
from pathlib import Path

import httpx

from loamz.identity.tenancy import tenant_alias

ENV = dict(
    line.split("=", 1)
    for line in Path(sys.argv[1]).read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
KC = f"http://localhost:{ENV.get('PORT_KEYCLOAK', '8080')}"
REALM = f"{KC}/realms/loamz"
AGENT = "http://localhost:8000/api/v1"
DATA = f"http://localhost:{ENV.get('PORT_DATA_API', '8020')}/api/v1"
# Keycloak only answers for its configured hostname; browsers and containers know it as keycloak:8080.
KC_HOST = {
    "Host": ENV.get("KEYCLOAK_PUBLIC_URL", "http://keycloak.localhost:8080").split("://", 1)[1]
}
http = httpx.Client(timeout=60)


def step(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        sys.exit(1)


def admin_token():
    r = http.post(
        f"{KC}/realms/master/protocol/openid-connect/token",
        headers=KC_HOST,
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ENV["KEYCLOAK_ADMIN_USER"],
            "password": ENV["KEYCLOAK_ADMIN_PASSWORD"],
        },
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}", **KC_HOST}


admin = admin_token()
api = f"{KC}/admin/realms/loamz"

# 1. organization + user + membership
alias = f"e2e{int(time.time())}"
r = http.post(
    f"{api}/organizations",
    headers=admin,
    json={"name": alias, "alias": alias, "domains": [{"name": f"{alias}.example.com"}]},
)
step("create organization", r.status_code == 201, str(r.status_code) + r.text[:200])
org_id = r.headers["Location"].rsplit("/", 1)[1]
r = http.post(
    f"{api}/users",
    headers=admin,
    json={
        "username": f"alice-{alias}",
        "email": f"alice@{alias}.example.com",
        "emailVerified": True,
        "enabled": True,
        "firstName": "Alice",
        "lastName": "E2E",
        "credentials": [{"type": "password", "value": "e2e-Passw0rd!", "temporary": False}],
    },
)
step("create user", r.status_code == 201, str(r.status_code) + r.text[:200])
user_id = r.headers["Location"].rsplit("/", 1)[1]
r = http.post(
    f"{api}/organizations/{org_id}/members",
    headers=admin,
    content=f'"{user_id}"',
)
r = (
    r
    if r.status_code < 400
    else http.post(
        f"{api}/organizations/{org_id}/members",
        headers={**admin, "Content-Type": "application/json"},
        content=f'"{user_id}"',
    )
)
step("add member", r.status_code in (201, 204), str(r.status_code) + r.text[:200])

# test-only: let the CLI client use the password grant so a script can sign in (the realm file is unchanged)
clients = http.get(f"{api}/clients", headers=admin, params={"clientId": "loamz-cli"}).json()
cli = clients[0]
http.put(
    f"{api}/clients/{cli['id']}",
    headers=admin,
    json={**cli, "directAccessGrantsEnabled": True},
).raise_for_status()


def user_token(scope):
    r = http.post(
        f"{REALM}/protocol/openid-connect/token",
        headers=KC_HOST,
        data={
            "grant_type": "password",
            "client_id": "loamz-cli",
            "username": f"alice-{alias}",
            "password": "e2e-Passw0rd!",
            "scope": scope,
        },
    )
    step(
        f"sign in ({scope})",
        r.status_code == 200,
        r.text[:300] if r.status_code != 200 else "",
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# 2. provision the tenant in loamz-data (as the platform would, with cell-admin)
r = http.post(
    f"{REALM}/protocol/openid-connect/token",
    headers=KC_HOST,
    data={
        "grant_type": "client_credentials",
        "client_id": "cell-admin",
        "client_secret": ENV["CELL_ADMIN_CLIENT_SECRET"],
    },
)
step("cell-admin token", r.status_code == 200, r.text[:200])
cell = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = http.post(
    f"{DATA}/tenants",
    headers=cell,
    json={"tenant_id": org_id, "alias": tenant_alias(org_id)},
)
step("provision tenant", r.status_code in (200, 201), str(r.status_code) + r.text[:300])

# 3. agent API as the user
user = user_token(f"openid organization:{alias}")
r = http.get(f"{AGENT}/threads", headers=user)
step("agent: list threads", r.status_code == 200, str(r.status_code) + r.text[:300])
r = http.post(
    f"{AGENT}/files/upload",
    headers=user,
    files={
        "file": (
            "notes.txt",
            b"Loamz cells keep data in the customer's cloud.\n",
            "text/plain",
        )
    },
)
step(
    "agent: upload a file (stored in loamz-data)",
    r.status_code in (200, 201),
    str(r.status_code) + r.text[:300],
)
file_id = r.json().get("file_id") or r.json().get("id")
r = http.get(f"{AGENT}/files/{file_id}/download", headers=user)
step(
    "agent: download it back",
    r.status_code == 200 and b"customer's cloud" in r.content,
    str(r.status_code) + r.text[:200],
)

# 4. data API directly as the same user: the file is in the tenant's governed volume
r = http.get(f"{DATA}/search", headers=user, params={"q": "tenant_"})
step("data: search the catalog", r.status_code == 200, str(r.status_code) + r.text[:300])

# 5. negative: a token for another organization's user cannot read alice's file
r = http.post(
    f"{api}/users",
    headers=admin,
    json={
        "username": f"mallory-{alias}",
        "enabled": True,
        "emailVerified": True,
        "email": f"m@{alias}.org",
        "firstName": "M",
        "lastName": "X",
        "credentials": [{"type": "password", "value": "e2e-Passw0rd!", "temporary": False}],
    },
)
r = http.post(
    f"{REALM}/protocol/openid-connect/token",
    headers=KC_HOST,
    data={
        "grant_type": "password",
        "client_id": "loamz-cli",
        "username": f"mallory-{alias}",
        "password": "e2e-Passw0rd!",
        "scope": "openid",
    },
)
mallory = {"Authorization": f"Bearer {r.json()['access_token']}"}
r = http.get(f"{AGENT}/files/{file_id}/download", headers=mallory)
step(
    "isolation: an outsider cannot read the file",
    r.status_code in (401, 403, 404),
    str(r.status_code),
)

# 6. chat: the agent answers a question about the uploaded file (needs an LLM key in .env)
if (
    ENV.get("OPENAI_API_KEY")
    or ENV.get("GEMINI_API_KEY")
    or ENV.get("ANTHROPIC_API_KEY")
    or ENV.get("OPENROUTER_API_KEY")
):
    r = http.post(f"{AGENT}/threads", headers=user, json={"name": "e2e"})
    step(
        "chat: create a thread",
        r.status_code in (200, 201),
        str(r.status_code) + r.text[:200],
    )
    thread = r.json()["id"]
    import json as _json

    body = {
        "thread_id": thread,
        "messages": [
            {
                "role": "user",
                "content": "Read the attached file and tell me in one sentence where cells keep data.",
            }
        ],
        "file_ids": [file_id],
    }
    answer, failed, events = "", False, []
    # The chat endpoint answers with the run's event stream (SSE).
    with http.stream("POST", f"{AGENT}/chat", headers=user, json=body, timeout=180) as r:
        step("chat: start a run", r.status_code == 200, str(r.status_code))
        for line in r.iter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            event = _json.loads(line[5:])
            events.append(event.get("type"))
            if event.get("type") == "text.delta":
                answer += event.get("delta") or event.get("text") or ""
            if event.get("type") in ("run.failed", "error"):
                failed = True
                answer = str(event)
            if event.get("type") in ("run.completed", "run.failed"):
                break
    step("chat: the answer streams back", answer != "" and not failed, answer[-300:])
    step("chat: it read the file", "cloud" in answer.lower(), answer[-300:])
else:
    print("SKIP chat (no LLM key in .env)")

print("ALL PASSED")
