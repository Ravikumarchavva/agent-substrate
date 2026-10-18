"""``GET /.well-known/data-token-jwks.json``: the public keys that verify this platform's data tokens.

The data service runs elsewhere and fetches this over HTTPS to trust tokens this platform mints for sandbox
runs. It is public by design (public keys only) and cacheable; when sandbox data access is off it is a 404.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from substrate_cloud.data_access import JWKS_PATH

router = APIRouter(tags=["infra"])


@router.get(JWKS_PATH, include_in_schema=False)
async def data_token_keys(request: Request) -> JSONResponse:
    access = getattr(request.app.state, "data_access", None)
    if access is None:
        raise HTTPException(404, "Sandbox data access is not enabled")
    return JSONResponse(
        access.minter.jwks(), headers={"Cache-Control": "public, max-age=300"}
    )
