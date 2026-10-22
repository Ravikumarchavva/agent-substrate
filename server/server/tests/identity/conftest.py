from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

ISSUER = "http://127.0.0.1:1/realms/loamz"
AUDIENCE = "agents-api"


class KeyServer:
    """A loopback JWKS endpoint (plus an OIDC discovery document) whose keys and health the tests control."""

    def __init__(self):
        self.keys: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.failing = False
        self.discovery_issuer = ISSUER
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.hits += 1
                if owner.failing:
                    self.send_response(503)
                    self.end_headers()
                    return
                if self.path.endswith("openid-configuration"):
                    body = {"issuer": owner.discovery_issuer, "jwks_uri": owner.url}
                else:
                    body = {"keys": list(owner.keys.values())}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    @property
    def url(self) -> str:
        return self.base + "/certs"

    def publish(self, kid: str, private_key: Any, alg: str = "RS256") -> None:
        public = private_key.public_key()
        if isinstance(private_key, rsa.RSAPrivateKey):
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(public, as_dict=True)
        else:
            jwk = jwt.algorithms.OKPAlgorithm.to_jwk(public, as_dict=True)
        self.keys[kid] = {**jwk, "kid": kid, "use": "sig", "alg": alg}

    def close(self):
        self.server.shutdown()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def server():
    s = KeyServer()
    yield s
    s.close()


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def ed_key():
    return ed25519.Ed25519PrivateKey.generate()


def mint(
    key: Any,
    *,
    alg: str = "RS256",
    kid: str = "k1",
    issuer: str = ISSUER,
    audience: Any = AUDIENCE,
    expires_in: int = 300,
    **claims: Any,
) -> str:
    now = int(time.time())
    body = {
        "iss": issuer,
        "aud": audience,
        "sub": "sub-alice",
        "preferred_username": "alice",
        "typ": "Bearer",
        "iat": now,
        "exp": now + expires_in,
        "organization": {"acme": {"id": "org-1"}},
        **claims,
    }
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, key, algorithm=alg, headers={"kid": kid})
