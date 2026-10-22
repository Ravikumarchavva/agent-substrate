"""Credentials that talk to an identity provider, and the chain that finds the right one for where you are.

    from loamz.identity import ClientSecretCredential, DefaultLoamzCredential

    DefaultLoamzCredential()                                   # whatever the environment provides (below)
    ClientSecretCredential(issuer, client_id, client_secret)   # a service account (OAuth client credentials)

`DefaultLoamzCredential` tries, in order, and uses the first that is configured:

1. `LOAMZ_TOKEN`: a ready bearer token;
2. `LOAMZ_CLIENT_ID` + `LOAMZ_CLIENT_SECRET` + `LOAMZ_ISSUER`: a service account;
3. the profile in `~/.loamz/config` (`LOAMZ_PROFILE` picks one; default `default`): `token`, or `client_id`,
   `client_secret` and `issuer`;
4. inside a Loamz code-interpreter sandbox: the per-run token file at `/run/loamz-data/token`.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import tomllib
from pathlib import Path

import httpx

from loamz.core.credentials import StaticTokenCredential, TokenCredential

SANDBOX_TOKEN_FILE = Path("/run/loamz-data/token")
CONFIG_FILE = Path("~/.loamz/config").expanduser()


class ClientSecretCredential:
    """OAuth 2.0 client credentials, cached until shortly before the token expires. Serves sync and async clients."""

    def __init__(self, issuer: str, client_id: str, client_secret: str, scope: str | None = None):
        self.url = issuer.rstrip("/") + "/protocol/openid-connect/token"
        self.form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            self.form["scope"] = scope
        self.lock = threading.Lock()
        self.alock: asyncio.Lock | None = None
        self.value: tuple[str, float] | None = None

    def _cached(self) -> str | None:
        if self.value and time.time() < self.value[1] - 30:
            return self.value[0]
        return None

    def _store(self, response: httpx.Response) -> str:
        response.raise_for_status()
        body = response.json()
        self.value = (body["access_token"], time.time() + int(body.get("expires_in", 60)))
        return self.value[0]

    def get_token(self) -> str:
        with self.lock:
            return self._cached() or self._store(httpx.post(self.url, data=self.form, timeout=10))

    async def aget_token(self) -> str:
        if self.alock is None:
            self.alock = asyncio.Lock()
        async with self.alock:
            cached = self._cached()
            if cached:
                return cached
            async with httpx.AsyncClient(timeout=10) as http:
                return self._store(await http.post(self.url, data=self.form))


def _profile(name: str) -> dict[str, str]:
    try:
        data = tomllib.loads(CONFIG_FILE.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    section = data.get(name, {})
    return {k: str(v) for k, v in section.items()} if isinstance(section, dict) else {}


def _from_settings(get: dict[str, str]) -> TokenCredential | None:
    if get.get("token"):
        return StaticTokenCredential(get["token"])
    if get.get("client_id") and get.get("client_secret") and get.get("issuer"):
        return ClientSecretCredential(
            get["issuer"], get["client_id"], get["client_secret"], get.get("scope") or None
        )
    return None


class DefaultLoamzCredential:
    """The first credential the environment provides (see the module docstring). Resolved on first use."""

    def __init__(
        self, *, profile: str | None = None, sandbox_token_file: Path = SANDBOX_TOKEN_FILE
    ):
        self._profile = profile
        self._sandbox = sandbox_token_file
        self._chosen: TokenCredential | None = None

    def _resolve(self) -> TokenCredential:
        if self._chosen is None:
            env = {
                "token": os.environ.get("LOAMZ_TOKEN", ""),
                "client_id": os.environ.get("LOAMZ_CLIENT_ID", ""),
                "client_secret": os.environ.get("LOAMZ_CLIENT_SECRET", ""),
                "issuer": os.environ.get("LOAMZ_ISSUER", ""),
                "scope": os.environ.get("LOAMZ_SCOPE", ""),
            }
            chosen = _from_settings(env) or _from_settings(
                _profile(self._profile or os.environ.get("LOAMZ_PROFILE", "default"))
            )
            if chosen is None and self._sandbox.exists():
                path = self._sandbox
                chosen = StaticTokenCredential(lambda: path.read_text().strip())
            if chosen is None:
                raise LookupError(
                    "no Loamz credential found: set LOAMZ_TOKEN, or LOAMZ_CLIENT_ID/LOAMZ_CLIENT_SECRET/LOAMZ_ISSUER, "
                    "or add a profile to ~/.loamz/config"
                )
            self._chosen = chosen
        return self._chosen

    def get_token(self) -> str:
        return self._resolve().get_token()

    async def aget_token(self) -> str:
        return await self._resolve().aget_token()


def default_endpoint(profile: str | None = None) -> str | None:
    """`LOAMZ_ENDPOINT`, else the profile's `endpoint`, else None."""
    return os.environ.get("LOAMZ_ENDPOINT") or _profile(
        profile or os.environ.get("LOAMZ_PROFILE", "default")
    ).get("endpoint")
