"""Migrate local workspace files and inline RAG images into the object store.

Run once when switching ``FILE_STORE_BACKEND`` from ``local`` to ``s3``:

    uv run python scripts/migrate_to_object_store.py --dry-run
    uv run python scripts/migrate_to_object_store.py

Two independent halves, either of which can be run alone:

1. **Workspace files** — copies ``{FILE_STORE_ROOT}/users/**`` into the object
   store under the identical key, so every existing ``FileMetadata.object_key``
   still resolves after the switch. Files are *copied*, not moved: the local
   tree stays intact, so the switch can be reverted by putting the env var back.
   Only ``users/`` is copied — anything at the root (e.g. a pre-``users/``
   layout's ``artifacts/``) has no owner to attribute it to.

2. **Inline RAG images** — rows written before images were stored by reference
   hold their PNG bytes in ``vector_documents_images.content_json``. This moves
   the bytes to ``users/{sub}/rag/{file_id}/p{n}-{i}.{ext}`` and leaves the row
   with its embedding plus an ``image_key``, matching what
   ``LocalRagBackend._ingest_images`` now writes. The owner is recovered from
   ``file_metadata.object_key`` because those rows predate ``user_id`` being
   recorded in image metadata.

3. **Version snapshots** — relocates ``.../.versions/{name}/{seq}{ext}`` to the
   sibling ``users/{uid}/versions/...`` prefix and rewrites
   ``file_versions.version_key`` to match. The old hidden directory sat inside
   the sandbox's working tree and, being dot-prefixed, was invisible to
   SeaweedFS's S3 ``LIST`` — so snapshots silently escaped per-user usage
   totals. See ``file_versioning.py`` for the layout rationale.

Idempotent: a workspace file already present with the same size is skipped, and
a row that already has ``image_key`` (or an already-relocated ``version_key``)
is left alone. Safe to re-run after a partial failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

from substrate.capabilities.storage.s3 import S3FileStore
from substrate.kernel.core.content import ImageBlock, TextBlock, content_block_from_dict

# ServerSettings, not SubstrateConfig: only the server layer loads `.env`, and
# this script is run from the same directory as `uv run start`.
from substrate.serving.shared.settings import ServerSettings

settings = ServerSettings()


def _build_store() -> S3FileStore:
    return S3FileStore(
        endpoint_url=settings.FILE_STORE_ENDPOINT or "",
        access_key=settings.FILE_STORE_ACCESS_KEY or "",
        secret_key=settings.FILE_STORE_SECRET_KEY or "",
        bucket=settings.FILE_STORE_BUCKET,
        region=settings.FILE_STORE_REGION,
        # Deliberately unlimited for the migration: existing data may already
        # exceed the per-user quota, and refusing to migrate it would leave the
        # user with files they can no longer read.
        user_quota_bytes=0,
    )


async def migrate_workspace_files(
    store: S3FileStore, *, dry_run: bool
) -> tuple[int, int]:
    root = Path(settings.FILE_STORE_ROOT).resolve()
    users_root = root / "users"
    if not users_root.is_dir():
        print(f"  no {users_root} — nothing to copy")
        return 0, 0

    copied = skipped = 0
    for path in sorted(p for p in users_root.rglob("*") if p.is_file()):
        key = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if await store.exists(key):
            skipped += 1
            continue
        print(f"  + {key} ({size:,} bytes)")
        if not dry_run:
            await store.upload(key, path.read_bytes())
        copied += 1
    return copied, skipped


async def migrate_inline_images(
    store: S3FileStore, *, dry_run: bool
) -> tuple[int, int]:
    # A plain engine, not init_db(): this script must not create/alter schema as
    # a side effect of a data migration.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    moved = untouched = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT id, content_json, metadata FROM vector_documents_images")
            )
        ).all()

        # file_id -> owning sub, recovered from the upload's object key.
        owners: dict[str, str] = {}
        for file_id, object_key in (
            await session.execute(
                text("SELECT id::text, object_key FROM file_metadata")
            )
        ).all():
            parts = str(object_key).split("/")
            if len(parts) >= 2 and parts[0] == "users":
                owners[file_id] = parts[1]

        for row_id, content_json, metadata in rows:
            meta = dict(metadata or {})
            if meta.get("image_key"):
                untouched += 1
                continue
            raw = (
                content_json
                if isinstance(content_json, list)
                else json.loads(content_json or "[]")
            )
            blocks = [content_block_from_dict(item) for item in raw]
            image = next((b for b in blocks if isinstance(b, ImageBlock)), None)
            if image is None or not image.data:
                untouched += 1
                continue

            file_id = str(meta.get("file_id") or "")
            user_id = str(meta.get("user_id") or owners.get(file_id) or "")
            if not user_id or not file_id:
                print(f"  ! {row_id}: no owner for file_id={file_id!r}; leaving inline")
                untouched += 1
                continue

            media_type = image.media_type or "image/png"
            ext = media_type.rsplit("/", 1)[-1] or "png"
            page = meta.get("page_number")
            name = f"p{page}-0.{ext}" if page is not None else f"0.{ext}"
            key = f"users/{user_id}/rag/{file_id}/{name}"
            print(f"  + {key} ({len(image.data):,} bytes)  [row {row_id}]")
            if dry_run:
                moved += 1
                continue

            await store.upload(key, image.data, content_type=media_type)
            placeholder = TextBlock(text=f"[{meta.get('label') or 'image'}]")
            meta.update(
                {"image_key": key, "media_type": media_type, "user_id": user_id}
            )
            await session.execute(
                text(
                    "UPDATE vector_documents_images "
                    "SET content_json = CAST(:content AS jsonb), "
                    "    metadata = CAST(:meta AS jsonb), "
                    "    text = :text "
                    "WHERE id = :id"
                ),
                {
                    "content": json.dumps([placeholder.model_dump(mode="json")]),
                    "meta": json.dumps(meta),
                    "text": placeholder.text,
                    "id": row_id,
                },
            )
            moved += 1
        if not dry_run:
            await session.commit()
    await engine.dispose()
    return moved, untouched


async def migrate_version_snapshots(
    store: S3FileStore, *, dry_run: bool
) -> tuple[int, int]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from substrate.serving.monolith.file_versioning import _version_key

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    moved = untouched = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT id, object_key, version_key, seq FROM file_versions")
            )
        ).all()
        for row_id, object_key, version_key, seq in rows:
            new_key = _version_key(str(object_key), int(seq))
            if new_key == version_key:
                untouched += 1
                continue
            try:
                data = await store.download(str(version_key))
            except Exception as exc:
                print(f"  ! {version_key}: unreadable ({exc}); leaving row alone")
                untouched += 1
                continue
            print(f"  {version_key}\n    → {new_key} ({len(data):,} bytes)")
            if dry_run:
                moved += 1
                continue
            await store.upload(new_key, data)
            await session.execute(
                text("UPDATE file_versions SET version_key = :k WHERE id = :id"),
                {"k": new_key, "id": row_id},
            )
            # Only drop the old copy once the new one is committed-readable.
            await store.delete(str(version_key))
            moved += 1
        if not dry_run:
            await session.commit()
    await engine.dispose()
    return moved, untouched


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would move, change nothing"
    )
    parser.add_argument("--files-only", action="store_true")
    parser.add_argument("--images-only", action="store_true")
    parser.add_argument("--versions-only", action="store_true")
    args = parser.parse_args()

    if not settings.FILE_STORE_ENDPOINT:
        print(
            "FILE_STORE_ENDPOINT is not set — point it at the object store "
            "(e.g. http://localhost:8333) before migrating.",
            file=sys.stderr,
        )
        return 1

    store = _build_store()
    await store.connect()
    print(
        f"Object store: {settings.FILE_STORE_ENDPOINT} bucket={settings.FILE_STORE_BUCKET}"
    )
    if args.dry_run:
        print("DRY RUN — nothing will be written\n")

    only = args.files_only or args.images_only or args.versions_only
    try:
        if not only or args.files_only:
            print("Workspace files:")
            copied, skipped = await migrate_workspace_files(store, dry_run=args.dry_run)
            print(f"  → {copied} copied, {skipped} already present\n")
        if not only or args.versions_only:
            print("Version snapshots:")
            relocated, kept = await migrate_version_snapshots(
                store, dry_run=args.dry_run
            )
            print(f"  → {relocated} relocated, {kept} already in place\n")
        if not only or args.images_only:
            print("Inline RAG images:")
            moved, untouched = await migrate_inline_images(store, dry_run=args.dry_run)
            print(f"  → {moved} moved, {untouched} left as-is\n")
    finally:
        await store.disconnect()

    print("Done." if not args.dry_run else "Dry run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
