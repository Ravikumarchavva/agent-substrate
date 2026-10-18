"""File metadata in the catalog database: heads, versions, quotas and pending uploads.

All SQL is written for both SQLite and PostgreSQL. It never uses LIKE (PostgreSQL's translation is
case-insensitive, and a prefix must be exact): prefixes are matched with `substr(path, 1, n) = prefix`
plus a lower bound, which keeps results correct under any collation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from substrate_data.catalog.store import CatalogStore
from substrate_data.core.errors import Problem, not_found
from substrate_data.files.quota import check_quota

MAX_COPY_FILES = 50_000
FILE_FIELDS = (
    "id",
    "path",
    "size",
    "sha256",
    "sha256_verified",
    "content_type",
    "version",
    "created_by",
    "created_at",
    "updated_at",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def prefix_clause(column: str, prefix: str) -> tuple[str, list[Any]]:
    if not prefix:
        return "", []
    return f" AND {column} >= ? AND substr({column}, 1, ?) = ?", [prefix, len(prefix), prefix]


def precondition_failed(detail: str = "The file changed; reload and retry") -> Problem:
    return Problem(412, "precondition-failed", detail)


class FileCatalog:
    def __init__(self, store: CatalogStore):
        self.store = store

    # ------------------------------------------------------------------ reads
    def head(self, volume_id: str, path: str, *, live: bool = True) -> dict[str, Any] | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM volume_files WHERE volume_id=? AND path=?", (volume_id, path)
            ).fetchone()
        if row is None or (live and row["deleted_at"] is not None):
            return None
        return dict(row)

    def current_object(self, volume_id: str, path: str, version: int | None = None) -> dict:
        """The version row (and so the object key) of a live file's current or given version."""
        file = self.head(volume_id, path)
        if file is None:
            raise not_found("File")
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM volume_file_versions WHERE file_id=? AND version=?",
                (file["id"], file["version"] if version is None else version),
            ).fetchone()
        if row is None or row["purged_at"] is not None:
            raise not_found("File version")
        return {**dict(row), "path": path}

    def live_bytes(self, volume_id: str, prefix: str = "", db: Any = None) -> int:
        clause, params = prefix_clause("path", prefix)
        sql = (
            "SELECT COALESCE(SUM(size), 0) AS total FROM volume_files "
            "WHERE volume_id=? AND deleted_at IS NULL" + clause
        )

        def run(conn):
            return int(conn.execute(sql, [volume_id, *params]).fetchone()["total"])

        if db is not None:
            return run(db)
        with self.store.connection() as conn:
            return run(conn)

    def usage(self, volume_id: str, prefix: str = "") -> dict[str, int | None]:
        clause, params = prefix_clause("path", prefix)
        with self.store.connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS files, COALESCE(SUM(size), 0) AS total FROM volume_files "
                "WHERE volume_id=? AND deleted_at IS NULL" + clause,
                [volume_id, *params],
            ).fetchone()
            retained = db.execute(
                "SELECT COALESCE(SUM(v.size), 0) AS total FROM volume_file_versions v "
                "JOIN volume_files f ON f.id=v.file_id WHERE v.volume_id=? AND v.purged_at IS NULL "
                "AND NOT (v.version=f.version AND f.deleted_at IS NULL)",
                (volume_id,),
            ).fetchone()
            volume_total = self.live_bytes(volume_id, "", db)
        return {
            "files": int(row["files"]),
            "bytes": int(row["total"]),
            "volume_bytes": volume_total,
            "retained_bytes": int(retained["total"]),
            "quota_bytes": self.quota(volume_id),
        }

    def quota(self, volume_id: str, db: Any = None) -> int | None:
        def run(conn):
            row = conn.execute(
                "SELECT max_bytes FROM volume_quotas WHERE volume_id=?", (volume_id,)
            ).fetchone()
            return None if row is None else int(row["max_bytes"])

        if db is not None:
            return run(db)
        with self.store.connection() as conn:
            return run(conn)

    def set_quota(self, volume_id: str, max_bytes: int | None, by: str) -> None:
        with self.store.connection() as db:
            if max_bytes is None:
                db.execute("DELETE FROM volume_quotas WHERE volume_id=?", (volume_id,))
                return
            changed = db.execute(
                "UPDATE volume_quotas SET max_bytes=?, updated_by=?, updated_at=? "
                "WHERE volume_id=?",
                (max_bytes, by, now(), volume_id),
            ).rowcount
            if not changed:
                db.execute(
                    "INSERT INTO volume_quotas (volume_id, max_bytes, updated_by, updated_at) "
                    "VALUES (?,?,?,?)",
                    (volume_id, max_bytes, by, now()),
                )

    def list_entries(
        self,
        volume_id: str,
        prefix: str,
        delimiter: str | None,
        after: str,
        limit: int,
        visible: Callable[[str], bool],
    ) -> tuple[list[dict], list[str], str | None]:
        """Files and (with a delimiter) directory prefixes in path order, one page.

        The cursor is the last returned key; a directory key ends with `/`, and its contents are
        skipped on the next page."""
        files: list[dict] = []
        directories: list[str] = []
        skip = after if after.endswith("/") else ""
        cursor, count, last, more = after, 0, "", False
        while not more:
            clause, params = prefix_clause("path", prefix)
            skip_sql, skip_params = ("", [])
            if skip:
                skip_sql, skip_params = " AND substr(path, 1, ?) <> ?", [len(skip), skip]
            with self.store.connection() as db:
                rows = db.execute(
                    "SELECT * FROM volume_files WHERE volume_id=? AND deleted_at IS NULL AND path > ?"
                    + clause
                    + skip_sql
                    + " ORDER BY path LIMIT ?",
                    [volume_id, cursor, *params, *skip_params, 500],
                ).fetchall()
            if not rows:
                break
            for row in rows:
                path = row["path"]
                cursor = path
                if skip and path.startswith(skip):
                    continue
                if delimiter and "/" in path[len(prefix) :]:
                    directory = prefix + path[len(prefix) :].split("/", 1)[0] + "/"
                    skip = directory
                    if not visible(directory):
                        continue
                    if count == limit:
                        more = True
                        break
                    directories.append(directory)
                    count, last = count + 1, directory
                    continue
                if not visible(path):
                    continue
                if count == limit:
                    more = True
                    break
                files.append(dict(row))
                count, last = count + 1, path
            if len(rows) < 500 and not more:
                break
        return files, directories, last if more else None

    def versions(self, volume_id: str, path: str, before: int | None, limit: int) -> list[dict]:
        file = self.head(volume_id, path, live=False)
        if file is None:
            raise not_found("File")
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM volume_file_versions WHERE file_id=?"
                + ("" if before is None else " AND version<?")
                + " ORDER BY version DESC LIMIT ?",
                [file["id"], *([] if before is None else [before]), limit],
            ).fetchall()
        return [
            {
                **dict(r),
                "path": path,
                "current": r["version"] == file["version"] and file["deleted_at"] is None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ writes
    def _guard_quota(self, db: Any, volume_id: str) -> int | None:
        """Take the volume's quota row (a write lock), so concurrent commits are checked in turn."""
        db.execute("UPDATE volume_quotas SET updated_at=updated_at WHERE volume_id=?", (volume_id,))
        return self.quota(volume_id, db)

    def _put_version(
        self, db: Any, volume_id: str, path: str, existing: dict | None, version: dict, by: str
    ) -> dict:
        stamp = now()
        values = {
            "size": version["size"],
            "sha256": version["sha256"],
            "sha256_verified": int(version["sha256_verified"]),
            "content_type": version["content_type"],
        }
        if existing is None:
            head = {
                "id": uuid4().hex,
                "volume_id": volume_id,
                "path": path,
                **values,
                "version": 1,
                "created_by": by,
                "created_at": stamp,
                "updated_at": stamp,
                "deleted_at": None,
            }
            db.execute(
                f"INSERT INTO volume_files ({', '.join(head)}) VALUES ({', '.join('?' * len(head))})",
                list(head.values()),
            )
        else:
            head = {
                **existing,
                **values,
                "version": existing["version"] + 1,
                "updated_at": stamp,
                "deleted_at": None,
            }
            db.execute(
                "UPDATE volume_files SET size=?, sha256=?, sha256_verified=?, content_type=?, "
                "version=?, updated_at=?, deleted_at=NULL WHERE id=?",
                (
                    values["size"],
                    values["sha256"],
                    values["sha256_verified"],
                    values["content_type"],
                    head["version"],
                    stamp,
                    head["id"],
                ),
            )
        db.execute(
            "INSERT INTO volume_file_versions (file_id, version, volume_id, object_key, size, "
            "sha256, sha256_verified, content_type, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                head["id"],
                head["version"],
                volume_id,
                version["object_key"],
                values["size"],
                values["sha256"],
                values["sha256_verified"],
                values["content_type"],
                by,
                stamp,
            ),
        )
        return head

    def commit_version(
        self,
        volume_id: str,
        path: str,
        version: dict[str, Any],
        by: str,
        *,
        if_match: int | None = None,
        if_none_match: bool = False,
    ) -> dict:
        """Make an uploaded object the file's new current version, atomically.

        `version` carries object_key, size, sha256, sha256_verified, content_type. Preconditions and
        the quota are decided inside the transaction that records the version."""
        with self.store.connection() as db:
            quota = self._guard_quota(db, volume_id)
            row = db.execute(
                "SELECT * FROM volume_files WHERE volume_id=? AND path=?", (volume_id, path)
            ).fetchone()
            existing = dict(row) if row else None
            live = existing is not None and existing["deleted_at"] is None
            if if_none_match and live:
                raise precondition_failed("The file already exists")
            if if_match is not None and (not live or existing["version"] != if_match):
                raise precondition_failed()
            if quota is not None:
                check_quota(
                    self.live_bytes(volume_id, "", db),
                    version["size"],
                    quota,
                    existing["size"] if live else 0,
                )
            return self._put_version(db, volume_id, path, existing, version, by)

    def soft_delete(self, volume_id: str, path: str, *, if_match: int | None = None) -> dict | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT * FROM volume_files WHERE volume_id=? AND path=?", (volume_id, path)
            ).fetchone()
            if row is None or row["deleted_at"] is not None:
                if if_match is not None:
                    raise precondition_failed()
                return None
            if if_match is not None and row["version"] != if_match:
                raise precondition_failed()
            db.execute("UPDATE volume_files SET deleted_at=? WHERE id=?", (now(), row["id"]))
            return dict(row)

    def delete_prefix(self, volume_id: str, prefix: str) -> tuple[int, int]:
        """Soft-delete every live file under `prefix`; returns (files, bytes)."""
        clause, params = prefix_clause("path", prefix)
        with self.store.connection() as db:
            total = db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS b FROM volume_files "
                "WHERE volume_id=? AND deleted_at IS NULL" + clause,
                [volume_id, *params],
            ).fetchone()
            db.execute(
                "UPDATE volume_files SET deleted_at=? WHERE volume_id=? AND deleted_at IS NULL"
                + clause,
                [now(), volume_id, *params],
            )
        return int(total["n"]), int(total["b"])

    def copy_prefix(
        self, volume_id: str, source: str, destination: str, by: str, *, overwrite: bool = True
    ) -> dict[str, int]:
        """Copy every live file under `source` to `destination` (sharing the stored objects).

        One transaction: a quota failure or a conflict leaves nothing half-copied."""
        with self.store.connection() as db:
            quota = self._guard_quota(db, volume_id)
            clause, params = prefix_clause("path", source)
            sources = db.execute(
                "SELECT * FROM volume_files WHERE volume_id=? AND deleted_at IS NULL"
                + clause
                + " ORDER BY path LIMIT ?",
                [volume_id, *params, MAX_COPY_FILES + 1],
            ).fetchall()
            if len(sources) > MAX_COPY_FILES:
                raise Problem(
                    413,
                    "too-many-files",
                    f"A prefix copy handles at most {MAX_COPY_FILES} files; copy sub-folders",
                )
            plan = []
            adding = replacing = skipped = 0
            for row in sources:
                target = destination + row["path"][len(source) :]
                existing = db.execute(
                    "SELECT * FROM volume_files WHERE volume_id=? AND path=?", (volume_id, target)
                ).fetchone()
                existing = dict(existing) if existing else None
                live = existing is not None and existing["deleted_at"] is None
                if live and not overwrite:
                    skipped += 1
                    continue
                adding += row["size"]
                replacing += existing["size"] if live else 0
                plan.append((row, target, existing))
            if quota is not None:
                check_quota(self.live_bytes(volume_id, "", db), adding, quota, replacing)
            for row, target, existing in plan:
                key = db.execute(
                    "SELECT object_key FROM volume_file_versions WHERE file_id=? AND version=?",
                    (row["id"], row["version"]),
                ).fetchone()["object_key"]
                self._put_version(
                    db,
                    volume_id,
                    target,
                    existing,
                    {
                        "object_key": key,
                        "size": row["size"],
                        "sha256": row["sha256"],
                        "sha256_verified": row["sha256_verified"],
                        "content_type": row["content_type"],
                    },
                    by,
                )
        return {"copied": len(plan), "skipped": skipped, "bytes": adding}

    def restore(
        self, volume_id: str, path: str, version: int | None, by: str, *, if_match: int | None
    ) -> dict:
        """Undelete a file, or make an older version current again as a new version."""
        with self.store.connection() as db:
            quota = self._guard_quota(db, volume_id)
            row = db.execute(
                "SELECT * FROM volume_files WHERE volume_id=? AND path=?", (volume_id, path)
            ).fetchone()
            if row is None:
                raise not_found("File")
            head = dict(row)
            live = head["deleted_at"] is None
            if if_match is not None and head["version"] != if_match:
                raise precondition_failed()
            if version is None or version == head["version"]:
                if live:
                    return head
                if quota is not None:
                    check_quota(self.live_bytes(volume_id, "", db), head["size"], quota)
                db.execute(
                    "UPDATE volume_files SET deleted_at=NULL, updated_at=? WHERE id=?",
                    (now(), head["id"]),
                )
                return {**head, "deleted_at": None}
            old = db.execute(
                "SELECT * FROM volume_file_versions WHERE file_id=? AND version=?",
                (head["id"], version),
            ).fetchone()
            if old is None or old["purged_at"] is not None:
                raise not_found("File version")
            if quota is not None:
                check_quota(
                    self.live_bytes(volume_id, "", db),
                    old["size"],
                    quota,
                    head["size"] if live else 0,
                )
            return self._put_version(db, volume_id, path, head, dict(old), by)

    # ------------------------------------------------------------------ pending uploads
    def create_upload(self, values: dict[str, Any]) -> dict[str, Any]:
        row = {"id": uuid4().hex, "created_at": now(), "state": "pending", **values}
        with self.store.connection() as db:
            db.execute(
                f"INSERT INTO volume_uploads ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()),
            )
        return row

    def upload(self, upload_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM volume_uploads WHERE id=?", (upload_id,)).fetchone()
        if row is None:
            raise not_found("Upload")
        return dict(row)

    def move_upload(self, upload_id: str, expected: str, state: str) -> bool:
        with self.store.connection() as db:
            return bool(
                db.execute(
                    "UPDATE volume_uploads SET state=? WHERE id=? AND state=?",
                    (state, upload_id, expected),
                ).rowcount
            )

    def pending_count(self, principal: str) -> int:
        with self.store.connection() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) AS n FROM volume_uploads WHERE created_by=? AND state='pending'",
                    (principal,),
                ).fetchone()["n"]
            )

    # ------------------------------------------------------------------ sweeping
    def expired_uploads(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM volume_uploads WHERE expires_at < ? LIMIT ?", (now(), limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def forget_upload(self, upload_id: str) -> None:
        with self.store.connection() as db:
            db.execute("DELETE FROM volume_uploads WHERE id=?", (upload_id,))

    def purge_versions(self, retention_days: int, limit: int = 1000) -> list[str]:
        """Mark superseded versions (and the versions of files deleted long ago) as purged and
        return the object keys nothing references any more."""
        if retention_days <= 0:
            return []
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT v.file_id, v.version, v.object_key FROM volume_file_versions v "
                "JOIN volume_files f ON f.id=v.file_id WHERE v.purged_at IS NULL AND ("
                "(v.version < f.version AND (SELECT n.created_at FROM volume_file_versions n "
                "WHERE n.file_id=v.file_id AND n.version=v.version+1) < ?) "
                "OR (f.deleted_at IS NOT NULL AND f.deleted_at < ?)) LIMIT ?",
                (cutoff, cutoff, limit),
            ).fetchall()
            stamp = now()
            for row in rows:
                db.execute(
                    "UPDATE volume_file_versions SET purged_at=? WHERE file_id=? AND version=?",
                    (stamp, row["file_id"], row["version"]),
                )
            garbage = []
            for key in {r["object_key"] for r in rows}:
                if not db.execute(
                    "SELECT 1 FROM volume_file_versions WHERE object_key=? AND purged_at IS NULL "
                    "LIMIT 1",
                    (key,),
                ).fetchone():
                    garbage.append(key)
            # A deleted file with nothing left is forgotten, so its path starts again at version 1.
            db.execute(
                "DELETE FROM volume_files WHERE deleted_at IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM volume_file_versions v WHERE v.file_id=volume_files.id "
                "AND v.purged_at IS NULL)"
            )
            db.execute(
                "DELETE FROM volume_file_versions WHERE purged_at IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM volume_files f WHERE f.id=volume_file_versions.file_id)"
            )
        return garbage

    def orphaned_volumes(self) -> list[str]:
        """Volumes that still have files but are no longer in the catalog (unregistered)."""
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT DISTINCT volume_id FROM volume_files WHERE volume_id NOT IN "
                "(SELECT id FROM catalog_assets)"
            ).fetchall()
        return [r["volume_id"] for r in rows]

    def drop_volume(self, volume_id: str) -> None:
        with self.store.connection() as db:
            db.execute("DELETE FROM volume_file_versions WHERE volume_id=?", (volume_id,))
            db.execute("DELETE FROM volume_files WHERE volume_id=?", (volume_id,))
            db.execute("DELETE FROM volume_quotas WHERE volume_id=?", (volume_id,))
