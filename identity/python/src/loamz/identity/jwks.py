"""An issuer's public keys: cached, refreshed on rotation (throttled), stale-if-error, then fail closed."""

from __future__ import annotations

import ssl
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jwt

from loamz.identity.errors import IdentityProviderUnavailable, InvalidToken

ALLOWED_ALGORITHMS = ("RS256", "ES256", "EdDSA")  # never HS* (shared secrets) and never `none`
MAX_BYTES = 256 * 1024


def check_url(url: str, *, insecure_http: bool) -> str:
    if url.startswith("https://") or (insecure_http and url.startswith("http://")):
        return url
    raise ValueError(
        f"{url!r} must be https (plain http needs insecure_http=True: tests and loopback only)"
    )


def make_client(ca_file: Path | str | None) -> httpx.Client:
    return httpx.Client(
        verify=ssl.create_default_context(cafile=str(ca_file)) if ca_file else True,
        timeout=httpx.Timeout(5),
        follow_redirects=False,
    )


class JwksSource:
    """Keys of one issuer.

    A token naming a key id not yet seen triggers one refresh (rotation needs no coordination), but never
    more than one per `min_refresh` seconds, so a caller spraying random key ids cannot turn this service
    into a request amplifier. If the issuer is unreachable, keys already fetched keep working for
    `stale_seconds`; after that the source fails closed (`IdentityProviderUnavailable`).

    `jwks_url` may be a callable (discovery): it is resolved lazily and only on a refresh.
    """

    def __init__(
        self,
        jwks_url: str | Callable[[], str],
        algorithms: tuple[str, ...] | list[str] = ALLOWED_ALGORITHMS,
        *,
        ca_file: Path | str | None = None,
        ttl: float = 300,
        min_refresh: float = 30,
        stale_seconds: float = 3600,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        insecure_http: bool = False,
    ):
        bad = set(algorithms) - set(ALLOWED_ALGORITHMS)
        if bad or not algorithms:
            raise ValueError(
                f"algorithms must be a non-empty subset of {ALLOWED_ALGORITHMS}: {bad}"
            )
        self.url = jwks_url
        self.algorithms = tuple(algorithms)
        self.ttl, self.min_refresh, self.stale = ttl, min_refresh, stale_seconds
        self.insecure_http = insecure_http
        self.client = client or make_client(ca_file)
        self.clock = clock
        self.lock = threading.Lock()
        self.keys: dict[str, tuple[Any, str | None]] = {}
        self.fetched = float("-inf")  # last successful fetch
        self.attempted = float("-inf")  # last attempt of any outcome
        if isinstance(jwks_url, str):
            check_url(jwks_url, insecure_http=insecure_http)

    def _resolve_url(self) -> str:
        url = self.url() if callable(self.url) else self.url
        return check_url(url, insecure_http=self.insecure_http)

    def _refresh(self) -> None:
        """Re-read the endpoint. The caller holds the lock."""
        self.attempted = self.clock()
        response = self.client.get(self._resolve_url(), headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > MAX_BYTES:
            raise ValueError("JWKS document is too large")
        keys: dict[str, tuple[Any, str | None]] = {}
        for entry in response.json().get("keys", []):
            alg = entry.get("alg")
            if alg not in (None, *self.algorithms) or entry.get("use") not in (None, "sig"):
                continue  # never trust a key for an algorithm the operator did not allow, nor an `enc` key
            try:
                jwk = jwt.PyJWK(entry)
            except jwt.PyJWTError:
                continue  # one unusable key must not hide the others
            keys[jwk.key_id or ""] = (jwk.key, alg)
        self.keys, self.fetched = keys, self.clock()

    def key_for(self, token: str) -> Any:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid") or ""
        with self.lock:
            now = self.clock()
            if (
                kid not in self.keys or now - self.fetched > self.ttl
            ) and now - self.attempted >= self.min_refresh:
                try:
                    self._refresh()
                except httpx.HTTPError, ValueError, KeyError, jwt.PyJWTError:
                    pass  # judged below: cached keys may still be inside the stale window
            if not self.keys or self.clock() - self.fetched > self.stale:
                raise IdentityProviderUnavailable("Cannot reach the token issuer's key endpoint")
            found = self.keys.get(kid)
        if found is None:
            raise InvalidToken("Access token rejected: unknown signing key")
        key, alg = found
        if alg is not None and header.get("alg") != alg:
            raise InvalidToken("Access token rejected: algorithm does not match the key")
        return key


def discover_jwks_uri(issuer: str, client: httpx.Client, *, insecure_http: bool) -> str:
    """Read `{issuer}/.well-known/openid-configuration`; the document must name the same issuer."""
    url = check_url(
        issuer.rstrip("/") + "/.well-known/openid-configuration", insecure_http=insecure_http
    )
    response = client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError("discovery document is too large")
    document = response.json()
    if document.get("issuer") != issuer:
        raise ValueError("discovery document names a different issuer")
    return str(document["jwks_uri"])
