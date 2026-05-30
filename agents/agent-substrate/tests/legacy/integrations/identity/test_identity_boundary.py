"""Tests for Section 5 identity web boundary.

Covers:
- decode_jwt_to_identity: happy path, expiry, bad signature, wrong type, missing sub
- PrincipalRecord ORM round-trips
- PostgresPrincipalStore: register/get/delete/list with an async session mock
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from ravi.adapters.identity import (
    JWTDecodeError,
    PostgresPrincipalStore,
    PrincipalNotFound,
    PrincipalRecord,
    PrincipalStore,
    decode_jwt_to_identity,
)
from ravi.kernel.runtime._identity import (
    IdentityContext,
    PrincipalId,
    PrincipalKind,
)

_SECRET = "test-secret-key-at-least-32-bytes-long!"  # >= 32 bytes for HS256
_ALG = "HS256"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    sub: str = "user-123",
    role: str = "end_user",
    tenant_id: str = "t1",
    workspace_id: str = "ws1",
    token_type: str = "access",
    expire_delta: timedelta = timedelta(minutes=30),
    extra: dict[str, Any] | None = None,
) -> str:
    claims: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "type": token_type,
        "jti": "jti-1",
        "exp": datetime.now(UTC) + expire_delta,
        "iat": datetime.now(UTC),
        **(extra or {}),
    }
    return jwt.encode(claims, _SECRET, algorithm=_ALG)


def _make_principal(
    name: str = "user-123",
    kind: PrincipalKind = PrincipalKind.HUMAN,
    tenant_id: str = "t1",
    workspace_id: str = "ws1",
    uid: str = "uid-001",
) -> PrincipalId:
    return PrincipalId(
        kind=kind,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name=name,
        uid=uid,
    )


# ===========================================================================
# decode_jwt_to_identity
# ===========================================================================


class TestDecodeJwtToIdentity:
    def test_happy_path_human_user(self) -> None:
        token = _make_token(sub="alice", role="end_user", tenant_id="acme", workspace_id="proj")
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)

        assert isinstance(ctx, IdentityContext)
        assert ctx.principal.name == "alice"
        assert ctx.principal.kind is PrincipalKind.HUMAN
        assert ctx.principal.tenant_id == "acme"
        assert ctx.principal.workspace_id == "proj"
        assert ctx.delegation_chain == ()

    def test_admin_role_maps_to_service_kind(self) -> None:
        token = _make_token(role="platform_admin")
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)
        assert ctx.principal.kind is PrincipalKind.SERVICE

    def test_agent_role_maps_to_agent_kind(self) -> None:
        token = _make_token(role="agent")
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)
        assert ctx.principal.kind is PrincipalKind.AGENT

    def test_unknown_role_defaults_to_human(self) -> None:
        token = _make_token(role="unknown_role")
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)
        assert ctx.principal.kind is PrincipalKind.HUMAN

    def test_uid_from_claims_when_present(self) -> None:
        token = _make_token(extra={"uid": "stable-uid-abc"})
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)
        assert ctx.principal.uid == "stable-uid-abc"

    def test_uid_generated_when_absent(self) -> None:
        token = _make_token()
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)
        # uid should be a non-empty hex string
        assert ctx.principal.uid
        assert len(ctx.principal.uid) == 32

    def test_expired_token_raises(self) -> None:
        token = _make_token(expire_delta=timedelta(seconds=-1))
        with pytest.raises(JWTDecodeError, match="expired"):
            decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)

    def test_wrong_secret_raises(self) -> None:
        token = _make_token()
        with pytest.raises(JWTDecodeError):
            decode_jwt_to_identity(token, "wrong-secret", algorithm=_ALG)

    def test_malformed_token_raises(self) -> None:
        with pytest.raises(JWTDecodeError, match="decode failed"):
            decode_jwt_to_identity("not.a.jwt", _SECRET, algorithm=_ALG)

    def test_wrong_token_type_raises(self) -> None:
        token = _make_token(token_type="refresh")
        with pytest.raises(JWTDecodeError, match="expected token type"):
            decode_jwt_to_identity(token, _SECRET, expected_type="access")

    def test_type_check_skipped_when_none(self) -> None:
        token = _make_token(token_type="service")
        ctx = decode_jwt_to_identity(token, _SECRET, expected_type=None)
        assert ctx.principal.name == "user-123"

    def test_default_tenant_and_workspace_when_absent(self) -> None:
        claims: dict[str, Any] = {
            "sub": "svc",
            "role": "service",
            "type": "access",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        }
        token = jwt.encode(claims, _SECRET, algorithm=_ALG)
        ctx = decode_jwt_to_identity(token, _SECRET, algorithm=_ALG)
        assert ctx.principal.tenant_id == "default"
        assert ctx.principal.workspace_id == "default"


# ===========================================================================
# PrincipalRecord ORM
# ===========================================================================


class TestPrincipalRecord:
    def test_from_principal_id_roundtrip(self) -> None:
        principal = _make_principal()
        record = PrincipalRecord.from_principal_id(principal)

        assert record.uid == principal.uid
        assert record.kind == principal.kind.name
        assert record.tenant_id == principal.tenant_id
        assert record.workspace_id == principal.workspace_id
        assert record.name == principal.name
        assert record.fqn == principal.fqn
        assert record.fingerprint == principal.fingerprint

    def test_to_principal_id_roundtrip(self) -> None:
        principal = _make_principal(kind=PrincipalKind.AGENT, name="my-agent", uid="a1b2")
        record = PrincipalRecord.from_principal_id(principal)
        reconstructed = record.to_principal_id()

        assert reconstructed.uid == principal.uid
        assert reconstructed.kind is principal.kind
        assert reconstructed.name == principal.name
        assert reconstructed.tenant_id == principal.tenant_id
        assert reconstructed.workspace_id == principal.workspace_id

    def test_all_principal_kinds_round_trip(self) -> None:
        for kind in PrincipalKind:
            p = PrincipalId(kind=kind, tenant_id="t", workspace_id="w", name="n")
            record = PrincipalRecord.from_principal_id(p)
            assert record.to_principal_id().kind is kind


# ===========================================================================
# PrincipalStore protocol conformance
# ===========================================================================


class TestPrincipalStoreConformance:
    def test_postgres_store_satisfies_protocol(self) -> None:
        store = PostgresPrincipalStore(session_factory=MagicMock())
        assert isinstance(store, PrincipalStore)


# ===========================================================================
# PostgresPrincipalStore
# ===========================================================================


def _make_session_factory(session: AsyncMock) -> MagicMock:
    """Return a factory whose __call__ returns an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return factory


class TestPostgresPrincipalStore:
    def _make_store(self, session: AsyncMock | None = None) -> tuple[PostgresPrincipalStore, AsyncMock]:
        if session is None:
            session = AsyncMock()
        factory = _make_session_factory(session)
        return PostgresPrincipalStore(session_factory=factory), session

    # ---- register -------------------------------------------------------

    async def test_register_inserts_new_principal(self) -> None:
        principal = _make_principal()
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()  # SQLAlchemy add() is synchronous
        session.commit = AsyncMock()
        store, _ = self._make_store(session)

        result = await store.register(principal)

        session.add.assert_called_once()
        session.commit.assert_called_once()
        assert result.uid == principal.uid

    async def test_register_updates_existing_principal(self) -> None:
        principal = _make_principal()
        existing = PrincipalRecord.from_principal_id(principal)
        session = AsyncMock()
        session.get = AsyncMock(return_value=existing)
        session.add = MagicMock()  # must not be called for updates
        session.commit = AsyncMock()
        store, _ = self._make_store(session)

        await store.register(principal)

        session.add.assert_not_called()
        session.commit.assert_called_once()

    # ---- get / get_or_none ----------------------------------------------

    async def test_get_returns_principal_when_found(self) -> None:
        principal = _make_principal()
        record = PrincipalRecord.from_principal_id(principal)
        session = AsyncMock()
        session.get = AsyncMock(return_value=record)
        store, _ = self._make_store(session)

        result = await store.get(principal.uid)
        assert result.uid == principal.uid
        assert result.kind is PrincipalKind.HUMAN

    async def test_get_raises_principal_not_found(self) -> None:
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        store, _ = self._make_store(session)

        with pytest.raises(PrincipalNotFound):
            await store.get("missing-uid")

    async def test_get_or_none_returns_none_when_missing(self) -> None:
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        store, _ = self._make_store(session)

        result = await store.get_or_none("no-such-uid")
        assert result is None

    # ---- get_by_fqn -----------------------------------------------------

    async def test_get_by_fqn_returns_principal(self) -> None:
        principal = _make_principal()
        record = PrincipalRecord.from_principal_id(principal)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=record)

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        store, _ = self._make_store(session)

        result = await store.get_by_fqn(principal.fqn)
        assert result is not None
        assert result.uid == principal.uid

    async def test_get_by_fqn_returns_none_when_missing(self) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        store, _ = self._make_store(session)

        result = await store.get_by_fqn("human/t/w/nonexistent")
        assert result is None

    # ---- list_for_tenant ------------------------------------------------

    async def test_list_for_tenant_returns_empty(self) -> None:
        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter([]))
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        store, _ = self._make_store(session)

        results = await store.list_for_tenant("empty-tenant")
        assert list(results) == []

    async def test_list_for_tenant_returns_principals(self) -> None:
        p1 = _make_principal(uid="uid-1", name="alice")
        p2 = _make_principal(uid="uid-2", name="bob")
        records = [PrincipalRecord.from_principal_id(p1), PrincipalRecord.from_principal_id(p2)]

        mock_scalars = MagicMock()
        mock_scalars.__iter__ = MagicMock(return_value=iter(records))
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=mock_scalars)

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        store, _ = self._make_store(session)

        results = list(await store.list_for_tenant("t1"))
        assert len(results) == 2
        assert {r.uid for r in results} == {"uid-1", "uid-2"}

    # ---- delete ---------------------------------------------------------

    async def test_delete_removes_existing_principal(self) -> None:
        principal = _make_principal()
        record = PrincipalRecord.from_principal_id(principal)
        session = AsyncMock()
        session.get = AsyncMock(return_value=record)
        session.commit = AsyncMock()
        store, _ = self._make_store(session)

        deleted = await store.delete(principal.uid)

        assert deleted is True
        session.delete.assert_called_once_with(record)
        session.commit.assert_called_once()

    async def test_delete_returns_false_when_absent(self) -> None:
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        store, _ = self._make_store(session)

        deleted = await store.delete("no-such-uid")
        assert deleted is False
        session.delete.assert_not_called()
