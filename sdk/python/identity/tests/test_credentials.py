"""Credentials that call an identity provider, used through the data client."""

from __future__ import annotations

import asyncio
import types

import httpx

from loamz.data import AsyncDataClient, DataClient
from loamz.identity import ClientSecretCredential


def bearer(request: httpx.Request) -> str:
    return request.headers["authorization"]


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses[0]


def client(*responses, credential):
    recorder = Recorder(*responses)
    api = DataClient("http://data.test", credential, transport=httpx.MockTransport(recorder))
    return api, recorder, []


def test_client_credentials_fetch_once_and_cache_until_near_expiry(monkeypatch):
    issued = []

    def fake_post(url, data, timeout):
        issued.append((url, dict(data)))
        return httpx.Response(
            200,
            json={"access_token": f"jwt-{len(issued)}", "expires_in": 300},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("loamz.identity.credentials.httpx", types.SimpleNamespace(post=fake_post))
    credentials = ClientSecretCredential(
        "https://idp/realms/x/", "svc", "s3cret", scope="data:read"
    )
    api, recorder, _ = client(httpx.Response(200, json={}), credential=credentials)
    api.request("GET", "/x")
    api.request("GET", "/x")
    assert len(issued) == 1 and issued[0][0] == "https://idp/realms/x/protocol/openid-connect/token"
    assert (
        issued[0][1]["grant_type"] == "client_credentials" and issued[0][1]["scope"] == "data:read"
    )
    assert [bearer(r) for r in recorder.requests] == ["Bearer jwt-1", "Bearer jwt-1"]


def test_client_credentials_work_with_the_async_client(monkeypatch):
    seen = []

    class FakeHttp:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            seen.append(url)
            return httpx.Response(
                200,
                json={"access_token": "async-jwt", "expires_in": 300},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "loamz.identity.credentials.httpx", types.SimpleNamespace(AsyncClient=FakeHttp)
    )
    recorder = Recorder(httpx.Response(200, json={}))

    async def run():
        async with AsyncDataClient(
            "http://data.test",
            ClientSecretCredential("https://idp/realms/x", "svc", "s"),
            transport=httpx.MockTransport(recorder),
        ) as api:
            await api.request("GET", "/x")
            await api.request("GET", "/x")

    asyncio.run(run())
    assert len(seen) == 1 and bearer(recorder.requests[1]) == "Bearer async-jwt"
