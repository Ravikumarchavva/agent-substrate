"""S3Connector — real AWS-compatibility (empty endpoint/credentials must
become ``None``, not be passed through as empty strings) and single-client
reuse across the connector's lifetime."""

from __future__ import annotations

from typing import Any

import pytest

from substrate.infrastructure.storage.s3 import S3Connector


class _FakeS3Client:
    """Records every call it receives; put_object/get_object/etc. are no-ops
    returning just enough shape for the connector's own code to not crash."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def put_object(self, **kwargs: Any) -> dict:
        self.calls.append(("put_object", kwargs))
        return {}

    async def delete_object(self, **kwargs: Any) -> dict:
        self.calls.append(("delete_object", kwargs))
        return {}


class _FakeClientContextManager:
    """Mimics aiobotocore's ``session.create_client(...)`` return value —
    an async context manager that yields the client on __aenter__."""

    def __init__(self, client: _FakeS3Client) -> None:
        self.client = client
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> _FakeS3Client:
        self.entered += 1
        return self.client

    async def __aexit__(self, *exc_info: Any) -> None:
        self.exited += 1


class _FakeSession:
    def __init__(self) -> None:
        self.create_client_calls: list[dict] = []
        self.client = _FakeS3Client()
        self.client_cm = _FakeClientContextManager(self.client)

    def create_client(self, service: str, **kwargs: Any) -> _FakeClientContextManager:
        assert service == "s3"
        self.create_client_calls.append(kwargs)
        return self.client_cm


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    import aiobotocore.session

    session = _FakeSession()
    monkeypatch.setattr(aiobotocore.session, "get_session", lambda: session)
    return session


async def test_empty_endpoint_and_credentials_become_none_not_empty_string(
    fake_session: _FakeSession,
):
    """boto3 needs None to trigger AWS's own default endpoint resolution and
    the standard credential chain -- an empty string is a different, invalid
    thing to botocore, not a synonym for 'unset'."""
    connector = S3Connector(endpoint_url="", access_key="", secret_key="")
    await connector.connect()

    kwargs = fake_session.create_client_calls[0]
    assert kwargs["endpoint_url"] is None
    assert kwargs["aws_access_key_id"] is None
    assert kwargs["aws_secret_access_key"] is None


async def test_real_values_pass_through_unchanged(fake_session: _FakeSession):
    connector = S3Connector(
        endpoint_url="http://localhost:9000", access_key="k", secret_key="s"
    )
    await connector.connect()

    kwargs = fake_session.create_client_calls[0]
    assert kwargs["endpoint_url"] == "http://localhost:9000"
    assert kwargs["aws_access_key_id"] == "k"
    assert kwargs["aws_secret_access_key"] == "s"


async def test_client_is_created_once_and_reused_across_operations(
    fake_session: _FakeSession,
):
    connector = S3Connector(
        endpoint_url="http://localhost:9000", access_key="k", secret_key="s"
    )
    await connector.connect()

    await connector.upload("a.bin", b"x")
    await connector.upload("b.bin", b"y")
    await connector.delete("a.bin")

    assert len(fake_session.create_client_calls) == 1
    assert fake_session.client_cm.entered == 1
    assert len(fake_session.client.calls) == 3


async def test_disconnect_closes_the_client(fake_session: _FakeSession):
    connector = S3Connector(endpoint_url="http://localhost:9000")
    await connector.connect()
    await connector.disconnect()

    assert fake_session.client_cm.exited == 1


async def test_operations_after_disconnect_raise(fake_session: _FakeSession):
    connector = S3Connector(endpoint_url="http://localhost:9000")
    await connector.connect()
    await connector.disconnect()

    with pytest.raises(AssertionError, match="Not connected"):
        await connector.upload("a.bin", b"x")
