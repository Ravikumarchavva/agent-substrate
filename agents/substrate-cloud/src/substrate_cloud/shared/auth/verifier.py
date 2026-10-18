"""The one token verifier every entry point of the platform uses (monolith and each microservice)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from substrate_identity import IdentityProviderUnavailable, PrimaryIssuer, TokenVerifier

from substrate_cloud.shared.settings import ServerSettings, settings


def build_verifier(config: ServerSettings) -> TokenVerifier:
    """Keycloak realm tokens only: RS256, audience ``OIDC_AUDIENCE``, exactly one organization. No trusted issuers here."""
    if not config.OIDC_ISSUER:
        raise IdentityProviderUnavailable(
            "OIDC_ISSUER is not configured; no token can be verified"
        )
    return TokenVerifier(
        PrimaryIssuer(
            config.OIDC_ISSUER,
            audience=config.OIDC_AUDIENCE,
            jwks_url=config.OIDC_JWKS_URL or None,
            ca_file=Path(config.OIDC_CA_FILE) if config.OIDC_CA_FILE else None,
        ),
        insecure_http=config.OIDC_INSECURE_HTTP,
    )


def verifier_for(app: FastAPI) -> TokenVerifier:
    """The app's verifier, built from settings on first use (tests and embedders may set ``app.state.token_verifier``)."""
    verifier = getattr(app.state, "token_verifier", None)
    if verifier is None:
        verifier = build_verifier(settings)
        app.state.token_verifier = verifier
    return verifier


def get_verifier(request: Request) -> TokenVerifier:
    return verifier_for(request.app)
