"""A document server reached by URL (``apps/document-intelligence``, or anything with the same two endpoints).

``POST {url}/v1/extract`` takes ``{"content_base64", "filename", "content_type", "strategy"}`` and answers with an ``ExtractionResult`` as
JSON — the same type the built-in reader returns. ``GET {url}/v1/health`` says whether it is up.

An answer from the server is **final**, including a refusal (a document the server's scanner blocked): it is never retried locally.
Only a server that cannot be reached at all — connection refused, timeout, 5xx — is reported with ``degraded_from="unreachable"`` so the
``Reader`` may fall back to its built-in engine.
"""

from __future__ import annotations

import base64
import logging

from substrate.documents.types import ExtractionResult, Strategy
from substrate.models.http import join_url, request_json
from substrate.types.errors import AuthError, PermanentError, RateLimitedError, ServiceUnavailableError

logger = logging.getLogger(__name__)


class RemoteExtractor:
    def __init__(self, url: str, *, api_key: str = "", timeout: float = 120.0) -> None:
        self.url = url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    async def read(
        self, data: bytes, filename: str, *, content_type: str | None = None, strategy: Strategy = "auto"
    ) -> ExtractionResult:
        body = {
            "content_base64": base64.b64encode(data).decode("ascii"),
            "filename": filename,
            "content_type": content_type or "",
            "strategy": strategy,
        }
        try:
            answer = await request_json("POST", join_url(self.url, "/v1/extract"), json_body=body, api_key=self._api_key, timeout=self._timeout)
            return ExtractionResult.model_validate(answer)
        except (ServiceUnavailableError, RateLimitedError) as exc:
            logger.warning("document server %s is unavailable: %s", self.url, exc)
            return ExtractionResult(success=False, error=f"the document server is unavailable: {exc}", engine=self.url, degraded_from="unreachable")
        except (AuthError, PermanentError) as exc:
            return ExtractionResult(success=False, error=f"the document server refused the request: {exc}", engine=self.url)
        except ValueError as exc:
            return ExtractionResult(success=False, error=f"the document server answered with something unreadable: {exc}", engine=self.url)

    async def healthy(self) -> bool:
        try:
            await request_json("GET", join_url(self.url, "/v1/health"), api_key=self._api_key, timeout=10)
            return True
        except Exception:  # noqa: BLE001
            return False


__all__ = ["RemoteExtractor"]
