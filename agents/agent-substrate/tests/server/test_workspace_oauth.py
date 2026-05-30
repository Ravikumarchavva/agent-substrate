from __future__ import annotations

import json
import time

from ravi.serving.monolith.routes.workspace_oauth import (
    get_workspace_access_token_async,
    store_workspace_tokens_async,
)


class _FakeRedis:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data: dict[str, str] = data or {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


async def test_store_workspace_tokens_records_expires_at() -> None:
    redis = _FakeRedis()

    await store_workspace_tokens_async(
        redis,
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=120,
    )

    raw = await redis.get("workspace_token:default_user")
    assert raw is not None

    payload = json.loads(raw)
    assert payload["access_token"] == "access-token"
    assert payload["refresh_token"] == "refresh-token"
    assert payload["expires_in"] == 120
    assert isinstance(payload["expires_at"], int)
    assert payload["expires_at"] >= int(time.time()) + 100


async def test_get_workspace_access_token_clears_legacy_payload_without_expiry() -> (
    None
):
    redis = _FakeRedis(
        {
            "workspace_token:default_user": json.dumps(
                {
                    "access_token": "legacy-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                }
            )
        }
    )

    token = await get_workspace_access_token_async(redis)

    assert token is None
    assert await redis.get("workspace_token:default_user") is None


async def test_get_workspace_access_token_clears_expired_payload() -> None:
    redis = _FakeRedis(
        {
            "workspace_token:default_user": json.dumps(
                {
                    "access_token": "expired-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                    "expires_at": int(time.time()) - 1,
                }
            )
        }
    )

    token = await get_workspace_access_token_async(redis)

    assert token is None
    assert await redis.get("workspace_token:default_user") is None
