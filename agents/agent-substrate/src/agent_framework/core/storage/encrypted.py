"""Envelope-encryption FileStore decorator.

Wraps any ``FileStore`` with AES-256-GCM encryption.  Each object gets a
random Data Encryption Key (DEK) which is itself encrypted by a Key
Encryption Key (KEK) obtained from a pluggable ``KeyProvider``.

Architecture::

    cleartext ─→ AES-256-GCM(DEK) ─→ ciphertext ─→ inner FileStore
                      │
                      └─ DEK encrypted with KEK ─→ stored as object metadata

Supported KEK sources:
  - ``LocalKeyProvider``: Static hex key from env var (dev / Docker Compose).
  - Extend ``KeyProvider`` for AWS KMS, Azure Key Vault, HashiCorp Vault.
"""

from __future__ import annotations

import os
import struct
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from agent_framework.core.storage.base import FileRef, FileStore


# ── Key Provider ABC ─────────────────────────────────────────────────────────


class KeyProvider(ABC):
    """Provides Key Encryption Keys (KEKs) for envelope encryption."""

    @abstractmethod
    async def encrypt_dek(self, dek: bytes) -> bytes:
        """Encrypt the data encryption key with the KEK.

        Returns the wrapped (encrypted) DEK.
        """

    @abstractmethod
    async def decrypt_dek(self, wrapped_dek: bytes) -> bytes:
        """Decrypt the wrapped DEK back to cleartext."""


class LocalKeyProvider(KeyProvider):
    """Static AES key loaded from a hex string.

    Suitable for development.  In production use an external KMS.

    Parameters:
        key_hex: 64-char hex string (32 bytes / 256 bits).
    """

    def __init__(self, key_hex: str) -> None:
        self._key = bytes.fromhex(key_hex)
        if len(self._key) != 32:
            raise ValueError("KEK must be exactly 32 bytes (64 hex chars)")

    async def encrypt_dek(self, dek: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, dek, None)
        return nonce + ct

    async def decrypt_dek(self, wrapped_dek: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = wrapped_dek[:12]
        ct = wrapped_dek[12:]
        return AESGCM(self._key).decrypt(nonce, ct, None)


# ── Encrypted FileStore ──────────────────────────────────────────────────────

# Envelope binary format:
#   [4 bytes: wrapped-DEK length (big-endian uint32)]
#   [wrapped-DEK bytes]
#   [12 bytes: AES-GCM nonce]
#   [remaining: ciphertext + 16-byte GCM tag]

_HEADER_FMT = ">I"  # big-endian unsigned 32-bit int


class EncryptedFileStore(FileStore):
    """Transparent envelope-encryption wrapper around any ``FileStore``.

    Parameters:
        inner: The underlying FileStore that actually persists data.
        key_provider: Provides encrypt/decrypt for the per-object DEK.
    """

    def __init__(self, inner: FileStore, key_provider: KeyProvider) -> None:
        self._inner = inner
        self._kp = key_provider

    async def startup(self) -> None:
        await self._inner.startup()

    async def shutdown(self) -> None:
        await self._inner.shutdown()

    # ── write ────────────────────────────────────────────────────────────

    async def put(
        self,
        key: str,
        data: bytes | AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> FileRef:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Materialize stream
        if isinstance(data, bytes):
            cleartext = data
        else:
            chunks: list[bytes] = []
            async for chunk in data:
                chunks.append(chunk)
            cleartext = b"".join(chunks)

        # Checksum of cleartext (before encryption)
        checksum = self._sha256(cleartext)

        # Generate random DEK and encrypt data
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, cleartext, None)

        # Wrap the DEK with the KEK
        wrapped_dek = await self._kp.encrypt_dek(dek)

        # Build envelope: [len(wrapped_dek)][wrapped_dek][nonce][ciphertext]
        header = struct.pack(_HEADER_FMT, len(wrapped_dek))
        envelope = header + wrapped_dek + nonce + ciphertext

        # Store envelope in inner store
        enc_meta = dict(metadata) if metadata else {}
        enc_meta["x-encryption"] = "aes-256-gcm-envelope"
        enc_meta["x-cleartext-sha256"] = checksum
        enc_meta["x-cleartext-size"] = str(len(cleartext))

        ref = await self._inner.put(
            key,
            envelope,
            content_type="application/octet-stream",
            metadata=enc_meta,
        )

        # Return a ref reflecting the cleartext, not the ciphertext
        return FileRef(
            object_key=ref.object_key,
            size_bytes=len(cleartext),
            content_type=content_type,
            checksum_sha256=checksum,
            created_at=ref.created_at,
            metadata=metadata or {},
        )

    # ── read ─────────────────────────────────────────────────────────────

    async def _decrypt_envelope(self, envelope: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        offset = 0
        (wrapped_len,) = struct.unpack_from(_HEADER_FMT, envelope, offset)
        offset += struct.calcsize(_HEADER_FMT)

        wrapped_dek = envelope[offset : offset + wrapped_len]
        offset += wrapped_len

        nonce = envelope[offset : offset + 12]
        offset += 12

        ciphertext = envelope[offset:]

        dek = await self._kp.decrypt_dek(wrapped_dek)
        return AESGCM(dek).decrypt(nonce, ciphertext, None)

    async def get(self, key: str) -> bytes:
        envelope = await self._inner.get(key)
        return await self._decrypt_envelope(envelope)

    async def get_stream(
        self, key: str, chunk_size: int = 1024 * 256
    ) -> AsyncIterator[bytes]:
        # Must decrypt entire blob (GCM is authenticated, can't chunk-decrypt)
        cleartext = await self.get(key)

        async def _iter() -> AsyncIterator[bytes]:
            for i in range(0, len(cleartext), chunk_size):
                yield cleartext[i : i + chunk_size]

        return _iter()

    async def get_url(self, key: str, *, expires_in: int = 3600) -> str:
        # Pre-signed URLs don't work with client-side encryption;
        # callers must stream through the server.
        raise NotImplementedError(
            "EncryptedFileStore cannot provide direct download URLs. "
            "Use get() or get_stream() and serve through the application."
        )

    # ── metadata / existence ─────────────────────────────────────────────

    async def exists(self, key: str) -> bool:
        return await self._inner.exists(key)

    async def head(self, key: str) -> FileRef:
        inner_ref = await self._inner.head(key)
        # Reconstruct cleartext metadata from stored envelope metadata
        cleartext_size = int(inner_ref.metadata.get("x-cleartext-size", "0"))
        cleartext_sha = inner_ref.metadata.get("x-cleartext-sha256", "")

        # Filter out internal encryption metadata
        user_meta = {
            k: v
            for k, v in inner_ref.metadata.items()
            if not k.startswith("x-encryption") and not k.startswith("x-cleartext")
        }

        return FileRef(
            object_key=inner_ref.object_key,
            size_bytes=cleartext_size or inner_ref.size_bytes,
            content_type=inner_ref.metadata.get(
                "x-original-content-type", inner_ref.content_type
            ),
            checksum_sha256=cleartext_sha,
            created_at=inner_ref.created_at,
            metadata=user_meta,
        )

    # ── delete ───────────────────────────────────────────────────────────

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._inner.delete_prefix(prefix)

    # ── list ─────────────────────────────────────────────────────────────

    async def list_keys(
        self,
        prefix: str = "",
        *,
        limit: int = 1000,
        cursor: Optional[str] = None,
    ) -> tuple[list[str], Optional[str]]:
        return await self._inner.list_keys(prefix, limit=limit, cursor=cursor)

    # ── copy ─────────────────────────────────────────────────────────────

    async def copy(self, src_key: str, dst_key: str) -> FileRef:
        # Re-encrypt: decrypt from src, re-encrypt to dst for key rotation safety
        cleartext = await self.get(src_key)
        src_ref = await self.head(src_key)
        return await self.put(
            dst_key,
            cleartext,
            content_type=src_ref.content_type,
            metadata=src_ref.metadata,
        )
