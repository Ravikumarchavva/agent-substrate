"""Two machines' worth of certificates for the end-to-end run, kept in separate directories.

    make_pki.py OUT_DIR

    OUT_DIR/data-side/   what the data service host holds: its server key and the CA that signs clients
    OUT_DIR/agent-side/  what the agent host holds: the CA to verify the data service, the gateway's
                         client certificate, and a server certificate for its public-key endpoint

Only the agent-side directory is ever mounted into the agent container.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from pki import issue, make_ca  # noqa: E402

out = Path(sys.argv[1])
data_side, agent_side = out / "data-side", out / "agent-side"
for directory in (data_side, agent_side):
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)

ca = make_ca(out)
ca_pem = ca[0].read_bytes()
(data_side / "ca.pem").write_bytes(ca_pem)
(agent_side / "ca.pem").write_bytes(ca_pem)
server = issue(data_side, ca, "data", dns=("host.docker.internal", "localhost"), ips=("127.0.0.1",))
gateway = issue(agent_side, ca, "gateway", client=True)
keys = issue(agent_side, ca, "keys", dns=("localhost",), ips=("127.0.0.1",))
for path in (*server, *gateway, *keys):
    path.chmod(0o644)  # the container runs as root but the files belong to the invoking user
print("pki ok")
