"""FileStore factory — builds the configured storage backend.

Usage::

    from agent_framework.core.storage.factory import create_file_store
    from agent_framework.configs.settings import settings

    store = create_file_store(settings)
    await store.startup()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_framework.core.storage.base import FileStore

if TYPE_CHECKING:
    from agent_framework.configs.settings import Settings

logger = logging.getLogger(__name__)


def create_file_store(settings: Settings) -> FileStore:
    """Instantiate a ``FileStore`` based on application settings.

    Reads ``FILE_STORE_BACKEND`` to choose the driver, then optionally
    wraps it with ``EncryptedFileStore`` when ``FILE_ENCRYPTION_MODE``
    is ``"envelope"``.
    """
    backend = settings.FILE_STORE_BACKEND.lower()

    if backend == "local":
        from agent_framework.core.storage.local import LocalFileStore

        root = settings.FILE_STORE_ROOT or str(settings.ROOT_DIR / "data" / "files")
        store: FileStore = LocalFileStore(root=root)
        logger.info("FileStore: local → %s", root)

    elif backend == "s3":
        from agent_framework.core.storage.s3 import S3FileStore

        store = S3FileStore(
            bucket=settings.FILE_STORE_BUCKET,
            endpoint_url=settings.FILE_STORE_ENDPOINT,
            region=settings.FILE_STORE_REGION,
            access_key=settings.FILE_STORE_ACCESS_KEY,
            secret_key=settings.FILE_STORE_SECRET_KEY,
            prefix=settings.FILE_STORE_PREFIX,
        )
        logger.info(
            "FileStore: s3 → bucket=%s endpoint=%s",
            settings.FILE_STORE_BUCKET,
            settings.FILE_STORE_ENDPOINT or "(default AWS)",
        )

    else:
        raise ValueError(
            f"Unknown FILE_STORE_BACKEND={backend!r}.  Supported values: 'local', 's3'."
        )

    # ── Optional encryption wrapper ──────────────────────────────────────
    enc_mode = settings.FILE_ENCRYPTION_MODE.lower()
    if enc_mode == "envelope":
        from agent_framework.core.storage.encrypted import (
            EncryptedFileStore,
            LocalKeyProvider,
        )

        if not settings.FILE_KEK_HEX:
            raise ValueError(
                "FILE_ENCRYPTION_MODE=envelope requires FILE_KEK_HEX "
                "(64-char hex string) to be set."
            )
        kp = LocalKeyProvider(settings.FILE_KEK_HEX)
        store = EncryptedFileStore(inner=store, key_provider=kp)
        logger.info("FileStore encryption: envelope (AES-256-GCM)")

    elif enc_mode not in ("none", ""):
        raise ValueError(
            f"Unknown FILE_ENCRYPTION_MODE={enc_mode!r}.  "
            "Supported values: 'none', 'envelope'."
        )

    return store
