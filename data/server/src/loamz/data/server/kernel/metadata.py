"""Control-plane metadata: securables, grants, audit and workload records.

Table *data and table metadata* live in Iceberg (object storage + the REST catalog). This store
holds what governs them: the catalog/schema/table namespace, ownership, grants, audit events and
the history of statements, imports and pipeline runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from loamz.data.server.kernel.errors import conflict, forbidden, not_found
from loamz.data.server.kernel.metadata_backends import Leader, PostgresBackend, SQLiteBackend
from loamz.data.server.kernel.principal import Principal
from loamz.data.storage.errors import (
    ConflictError,
    InvalidRequestError,
    LoamzError,
    PreconditionFailedError,
)

NAME = r"^[a-z_][a-z0-9_]{0,63}$"
PRIVILEGES = {
    "catalog": {
        "USE_CATALOG",
        "CREATE_SCHEMA",
        "SELECT",
        "MODIFY",
        "CREATE_TABLE",
        "CREATE_VOLUME",
        "CREATE_MODEL",
        "READ_VOLUME",
        "WRITE_VOLUME",
        "USE_MODEL",
        "USE_SCHEMA",
    },
    "schema": {
        "USE_SCHEMA",
        "CREATE_TABLE",
        "CREATE_VOLUME",
        "CREATE_MODEL",
        "SELECT",
        "MODIFY",
        "READ_VOLUME",
        "WRITE_VOLUME",
        "USE_MODEL",
    },
    "table": {"SELECT", "MODIFY"},
    "view": {"SELECT"},
    "volume": {"READ_VOLUME", "WRITE_VOLUME"},
    "model": {"USE_MODEL"},
    "compute": {"USE_COMPUTE"},
}
ALL = "ALL_PRIVILEGES"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS catalogs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, comment TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS schemas (
    id TEXT PRIMARY KEY, catalog_id TEXT NOT NULL REFERENCES catalogs(id), name TEXT NOT NULL,
    owner TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, UNIQUE (catalog_id, name));
CREATE TABLE IF NOT EXISTS tables (
    id TEXT PRIMARY KEY, schema_id TEXT NOT NULL REFERENCES schemas(id), name TEXT NOT NULL,
    owner TEXT NOT NULL, comment TEXT, format TEXT NOT NULL DEFAULT 'iceberg',
    partitioning TEXT NOT NULL DEFAULT '[]', columns TEXT NOT NULL DEFAULT '[]',
    row_count INTEGER NOT NULL DEFAULT 0, size_bytes INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0, snapshot_id TEXT, parent_table_id TEXT,
    materialized TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (schema_id, name));
CREATE TABLE IF NOT EXISTS views (
    id TEXT PRIMARY KEY, schema_id TEXT NOT NULL REFERENCES schemas(id), name TEXT NOT NULL,
    owner TEXT NOT NULL, comment TEXT, sql TEXT NOT NULL, columns TEXT NOT NULL DEFAULT '[]',
    definer TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, UNIQUE (schema_id, name));
CREATE TABLE IF NOT EXISTS catalog_assets (
    id TEXT PRIMARY KEY, schema_id TEXT NOT NULL REFERENCES schemas(id), kind TEXT NOT NULL,
    name TEXT NOT NULL, owner TEXT NOT NULL, comment TEXT, properties TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(schema_id,kind,name));
CREATE TABLE IF NOT EXISTS grants (
    securable_type TEXT NOT NULL, securable_id TEXT NOT NULL, principal TEXT NOT NULL,
    privilege TEXT NOT NULL, granted_by TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (securable_type, securable_id, principal, privilege));
CREATE INDEX IF NOT EXISTS grants_principal ON grants(principal);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, request_id TEXT, principal TEXT,
    actor TEXT, action TEXT NOT NULL, securable TEXT, outcome TEXT NOT NULL, detail TEXT,
    prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS statements (
    id TEXT PRIMARY KEY, principal TEXT NOT NULL, actor TEXT, sql TEXT NOT NULL,
    status TEXT NOT NULL, resource_id TEXT, tables TEXT NOT NULL DEFAULT '[]',
    context TEXT NOT NULL DEFAULT '{}',
    row_count INTEGER, duration_ms REAL, error TEXT, created_at TEXT NOT NULL, finished_at TEXT);
CREATE INDEX IF NOT EXISTS statements_principal ON statements(principal, created_at);
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY, principal TEXT NOT NULL, table_id TEXT, target TEXT NOT NULL,
    format TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, staging_key TEXT,
    upload_id TEXT, size_bytes INTEGER, job_id TEXT, rows INTEGER, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pipelines (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL, source_table_id TEXT NOT NULL,
    target_schema_id TEXT NOT NULL, target_name TEXT NOT NULL, definition TEXT NOT NULL,
    compute_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL REFERENCES pipelines(id), principal TEXT,
    job_id TEXT, status TEXT NOT NULL, output_table_id TEXT, rows INTEGER, error TEXT,
    created_at TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS usage_daily (
    principal TEXT NOT NULL, day TEXT NOT NULL, statements INTEGER NOT NULL DEFAULT 0,
    bytes_planned INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (principal, day));
CREATE TABLE IF NOT EXISTS table_policies (
    table_id TEXT PRIMARY KEY REFERENCES tables(id), row_filter TEXT,
    column_masks TEXT NOT NULL DEFAULT '[]', exempt TEXT NOT NULL DEFAULT '[]',
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS maintenance_runs (
    id TEXT PRIMARY KEY, table_id TEXT NOT NULL REFERENCES tables(id), principal TEXT NOT NULL,
    kind TEXT NOT NULL, params TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, job_id TEXT,
    files_in INTEGER, files_out INTEGER, rows INTEGER, bytes_before INTEGER, bytes_after INTEGER,
    error TEXT, created_at TEXT NOT NULL, finished_at TEXT);
CREATE INDEX IF NOT EXISTS maintenance_table ON maintenance_runs(table_id, created_at);
CREATE TABLE IF NOT EXISTS publications (
    job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS volume_files (
    id TEXT PRIMARY KEY, volume_id TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
    sha256 TEXT NOT NULL, sha256_verified INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL, version INTEGER NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
    UNIQUE (volume_id, path));
CREATE INDEX IF NOT EXISTS volume_files_deleted ON volume_files(deleted_at);
CREATE TABLE IF NOT EXISTS volume_file_versions (
    file_id TEXT NOT NULL, version INTEGER NOT NULL, volume_id TEXT NOT NULL,
    object_key TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
    sha256_verified INTEGER NOT NULL DEFAULT 0, content_type TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    purged_at TEXT, PRIMARY KEY (file_id, version));
CREATE INDEX IF NOT EXISTS volume_file_versions_object ON volume_file_versions(object_key);
CREATE TABLE IF NOT EXISTS volume_quotas (
    volume_id TEXT PRIMARY KEY, max_bytes INTEGER NOT NULL, updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS volume_uploads (
    id TEXT PRIMARY KEY, volume_id TEXT, path TEXT, object_key TEXT NOT NULL, upload_id TEXT,
    size INTEGER NOT NULL, content_type TEXT NOT NULL, sha256 TEXT NOT NULL,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS volume_uploads_expiry ON volume_uploads(state, expires_at);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Securable:
    """A node in the catalog → schema → table chain, from the root down."""

    type: str
    id: str
    owner: str


def decode(
    row: Mapping[str, Any] | None, json_fields: tuple[str, ...] = ()
) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for name in json_fields:
        if value.get(name) is not None:
            value[name] = json.loads(value[name])
    return value


TABLE_JSON = ("partitioning", "columns", "materialized")
VIEW_JSON = ("columns", "definer")


MIGRATIONS = (
    # Columns added after a catalog was first created (CREATE IF NOT EXISTS skips them).
    ("statements", "context", "TEXT NOT NULL DEFAULT '{}'"),
    ("tables", "materialized", "TEXT"),
    ("audit_events", "prev_hash", "TEXT"),
    ("audit_events", "hash", "TEXT"),
)


class CatalogStore:
    """Governed metadata. SQLite for development and single nodes; PostgreSQL (`url`) for production."""

    def __init__(self, path: Path | None = None, *, url: str = "", pool_size: int = 16):
        if url:
            self.backend: SQLiteBackend | PostgresBackend = PostgresBackend(
                url, SCHEMA, MIGRATIONS, pool_size
            )
        elif path is not None:
            self.backend = SQLiteBackend(path, SCHEMA, MIGRATIONS)
        else:
            raise ValueError("A catalog needs a SQLite path or a PostgreSQL URL")
        self.path = path

    def connection(self):
        """One pooled connection per thread; each `with` block is one transaction."""
        return self.backend.connection()

    def leader(self, name: str) -> Leader:
        """Whether this replica should run work only one replica may do."""
        return self.backend.leader(name)

    def close(self) -> None:
        self.backend.close()

    # ------------------------------------------------------------------ namespace
    def ensure_default(self, admin_group: str) -> None:
        """Create `main.default` on first start: admins own it, every user may browse it."""
        with self.connection() as db:
            if db.execute("SELECT 1 FROM catalogs WHERE name='main'").fetchone():
                return
            owner = f"group:{admin_group}"
            try:
                catalog = self._insert(db, "catalogs", {"name": "main", "owner": owner})
            except ConflictError:
                return  # another replica created it first
            schema = self._insert(
                db, "schemas", {"catalog_id": catalog["id"], "name": "default", "owner": owner}
            )
            for securable_type, securable_id, privilege in [
                ("catalog", catalog["id"], "USE_CATALOG"),
                ("schema", schema["id"], "USE_SCHEMA"),
            ]:
                db.execute(
                    "INSERT OR IGNORE INTO grants VALUES (?,?,?,?,?,?)",
                    (
                        securable_type,
                        securable_id,
                        "group:account users",
                        privilege,
                        "system",
                        now(),
                    ),
                )

    def _insert(self, db: Any, table: str, values: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        row = {"id": uuid4().hex, "created_at": stamp, "updated_at": stamp, "version": 1, **values}
        columns = ", ".join(row)
        try:
            db.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' * len(row))})",
                [json.dumps(v) if isinstance(v, (list, dict)) else v for v in row.values()],
            )
        except self.backend.integrity_errors:
            raise conflict(f"{values.get('name', 'Record')} already exists") from None
        return row

    def create_catalog(self, name: str, owner: str, comment: str | None) -> dict[str, Any]:
        with self.connection() as db:
            return self._insert(db, "catalogs", {"name": name, "owner": owner, "comment": comment})

    def create_schema(
        self, catalog: dict[str, Any], name: str, owner: str, comment: str | None
    ) -> dict[str, Any]:
        with self.connection() as db:
            return self._insert(
                db,
                "schemas",
                {"catalog_id": catalog["id"], "name": name, "owner": owner, "comment": comment},
            )

    def create_table(self, schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as db:
            if db.execute(
                "SELECT 1 FROM views WHERE schema_id=? AND name=?", (schema["id"], values["name"])
            ).fetchone():
                raise conflict(f"{values['name']} already exists as a view")
            return self._insert(db, "tables", {"schema_id": schema["id"], **values})

    def catalog(self, name: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM catalogs WHERE name=?", (name,)).fetchone()
        if row is None:
            raise not_found(f"Catalog {name}")
        return dict(row)

    def schema(self, catalog_name: str, name: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                "SELECT s.*, c.name AS catalog_name, c.owner AS catalog_owner FROM schemas s "
                "JOIN catalogs c ON c.id=s.catalog_id WHERE c.name=? AND s.name=?",
                (catalog_name, name),
            ).fetchone()
        if row is None:
            raise not_found(f"Schema {catalog_name}.{name}")
        return dict(row)

    TABLE_SELECT = (
        "SELECT t.*, s.name AS schema_name, s.owner AS schema_owner, s.id AS schema_id, "
        "c.name AS catalog_name, c.owner AS catalog_owner, c.id AS catalog_id FROM tables t "
        "JOIN schemas s ON s.id=t.schema_id JOIN catalogs c ON c.id=s.catalog_id "
    )

    def table(self, catalog_name: str, schema_name: str, name: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                self.TABLE_SELECT + "WHERE c.name=? AND s.name=? AND t.name=?",
                (catalog_name, schema_name, name),
            ).fetchone()
        if row is None:
            raise not_found(f"Table {catalog_name}.{schema_name}.{name}")
        return decode(row, TABLE_JSON)

    def materialized_tables(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(self.TABLE_SELECT + "WHERE t.materialized IS NOT NULL").fetchall()
        return [decode(r, TABLE_JSON) for r in rows]

    def table_by_id(self, table_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(self.TABLE_SELECT + "WHERE t.id=?", (table_id,)).fetchone()
        if row is None:
            raise not_found("Table")
        return decode(row, TABLE_JSON)

    def tables_by_names(
        self, names: list[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], dict]:
        """Resolve many three-part names in one indexed query."""
        if not names:
            return {}
        clause = " OR ".join("(c.name=? AND s.name=? AND t.name=?)" for _ in names)
        with self.connection() as db:
            rows = db.execute(
                self.TABLE_SELECT + "WHERE " + clause, [part for name in names for part in name]
            ).fetchall()
        found = {}
        for row in rows:
            value = decode(row, TABLE_JSON)
            found[(value["catalog_name"], value["schema_name"], value["name"])] = value
        return found

    VIEW_SELECT = (
        "SELECT v.*, s.name AS schema_name, s.owner AS schema_owner, s.id AS schema_id, "
        "c.name AS catalog_name, c.owner AS catalog_owner, c.id AS catalog_id FROM views v "
        "JOIN schemas s ON s.id=v.schema_id JOIN catalogs c ON c.id=s.catalog_id "
    )

    def create_asset(self, kind: str, schema: dict[str, Any], values: dict[str, Any]) -> dict:
        if kind not in {"volume", "model"}:
            raise ValueError("Unsupported catalog asset kind")
        with self.connection() as db:
            return self._insert(
                db, "catalog_assets", {"schema_id": schema["id"], "kind": kind, **values}
            )

    def asset(self, kind: str, catalog: str, schema: str, name: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                """SELECT a.*,s.name AS schema_name,c.name AS catalog_name,
                s.owner AS schema_owner,c.owner AS catalog_owner,c.id AS catalog_id
                FROM catalog_assets a JOIN schemas s ON a.schema_id=s.id
                JOIN catalogs c ON s.catalog_id=c.id
                WHERE a.kind=? AND c.name=? AND s.name=? AND a.name=?""",
                (kind, catalog, schema, name),
            ).fetchone()
        if row is None:
            raise not_found(f"{kind} {catalog}.{schema}.{name}")
        return decode(row, ("properties",))

    def list_assets(
        self, kind: str, catalog: str, schema: str, after: str = "", limit: int = 1000
    ) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT a.*,s.name AS schema_name,c.name AS catalog_name,
                s.owner AS schema_owner,c.owner AS catalog_owner,c.id AS catalog_id
                FROM catalog_assets a JOIN schemas s ON a.schema_id=s.id
                JOIN catalogs c ON s.catalog_id=c.id
                WHERE a.kind=? AND c.name=? AND s.name=? AND a.name>? ORDER BY a.name LIMIT ?""",
                (kind, catalog, schema, after, limit),
            ).fetchall()
        return [decode(row, ("properties",)) for row in rows]

    def create_view(self, schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as db:
            if db.execute(
                "SELECT 1 FROM tables WHERE schema_id=? AND name=?", (schema["id"], values["name"])
            ).fetchone():
                raise conflict(f"{values['name']} already exists as a table")
            return self._insert(db, "views", {"schema_id": schema["id"], **values})

    def view(self, catalog_name: str, schema_name: str, name: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(
                self.VIEW_SELECT + "WHERE c.name=? AND s.name=? AND v.name=?",
                (catalog_name, schema_name, name),
            ).fetchone()
        if row is None:
            raise not_found(f"View {catalog_name}.{schema_name}.{name}")
        return decode(row, VIEW_JSON)

    def views_by_names(self, names: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], dict]:
        if not names:
            return {}
        clause = " OR ".join("(c.name=? AND s.name=? AND v.name=?)" for _ in names)
        with self.connection() as db:
            rows = db.execute(
                self.VIEW_SELECT + "WHERE " + clause, [part for name in names for part in name]
            ).fetchall()
        found = {}
        for row in rows:
            value = decode(row, VIEW_JSON)
            found[(value["catalog_name"], value["schema_name"], value["name"])] = value
        return found

    def list_views(self, schema: dict[str, Any], after: str, limit: int) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                decode(r, VIEW_JSON)
                for r in db.execute(
                    self.VIEW_SELECT + "WHERE v.schema_id=? AND v.name>? ORDER BY v.name LIMIT ?",
                    (schema["id"], after, limit),
                )
            ]

    def search(self, text: str, limit: int) -> list[tuple[str, dict[str, Any]]]:
        """Candidate tables and views whose name, comment or column list contains `text`.

        Newest first, capped; the caller filters by what the principal may browse."""
        pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        found: list[tuple[str, dict[str, Any]]] = []
        with self.connection() as db:
            for kind, select, json_fields, alias in (
                ("table", self.TABLE_SELECT, TABLE_JSON, "t"),
                ("view", self.VIEW_SELECT, VIEW_JSON, "v"),
            ):
                rows = db.execute(
                    select + f"WHERE ({alias}.name LIKE ? ESCAPE '\\' OR {alias}.comment LIKE ? "
                    f"ESCAPE '\\' OR {alias}.columns LIKE ? ESCAPE '\\') "
                    f"ORDER BY {alias}.updated_at DESC LIMIT ?",
                    (pattern, pattern, pattern, limit),
                ).fetchall()
                found += [(kind, decode(r, json_fields)) for r in rows]
        return found

    def list_catalogs(self, after: str, limit: int) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM catalogs WHERE name>? ORDER BY name LIMIT ?", (after, limit)
                )
            ]

    def list_schemas(self, catalog: dict[str, Any], after: str, limit: int) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                {**dict(r), "catalog_name": catalog["name"]}
                for r in db.execute(
                    "SELECT * FROM schemas WHERE catalog_id=? AND name>? ORDER BY name LIMIT ?",
                    (catalog["id"], after, limit),
                )
            ]

    def list_tables(self, schema: dict[str, Any], after: str, limit: int) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                decode(r, TABLE_JSON)
                for r in db.execute(
                    self.TABLE_SELECT + "WHERE t.schema_id=? AND t.name>? ORDER BY t.name LIMIT ?",
                    (schema["id"], after, limit),
                )
            ]

    def update_table_state(self, table_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Record the current Iceberg snapshot and statistics after a commit."""
        assignments = ", ".join(f"{k}=?" for k in values)
        with self.connection() as db:
            db.execute(
                f"UPDATE tables SET {assignments}, updated_at=?, version=version+1 WHERE id=?",
                [
                    *(json.dumps(v) if isinstance(v, (list, dict)) else v for v in values.values()),
                    now(),
                    table_id,
                ],
            )
        return self.table_by_id(table_id)

    def update_securable(
        self, kind: str, record: dict[str, Any], values: dict[str, Any], if_match: int | None
    ) -> None:
        table = {
            "catalog": "catalogs",
            "schema": "schemas",
            "table": "tables",
            "view": "views",
            "volume": "catalog_assets",
            "model": "catalog_assets",
        }[kind]
        assignments = ", ".join(f"{k}=?" for k in values)
        guard, extra = ("", []) if if_match is None else (" AND version=?", [if_match])
        with self.connection() as db:
            changed = db.execute(
                f"UPDATE {table} SET {assignments}, updated_at=?, version=version+1 "
                f"WHERE id=?{guard}",
                [*values.values(), now(), record["id"], *extra],
            ).rowcount
        if not changed:
            raise PreconditionFailedError("The resource changed; reload and retry")

    def delete_securable(self, kind: str, record: dict[str, Any], if_match: int | None) -> None:
        table = {
            "catalog": "catalogs",
            "schema": "schemas",
            "table": "tables",
            "view": "views",
            "volume": "catalog_assets",
            "model": "catalog_assets",
        }[kind]
        children = {
            "catalog": [("schemas", "catalog_id")],
            "schema": [
                ("tables", "schema_id"),
                ("views", "schema_id"),
                ("catalog_assets", "schema_id"),
            ],
        }.get(kind, [])
        with self.connection() as db:
            if any(
                db.execute(f"SELECT 1 FROM {t} WHERE {c}=? LIMIT 1", (record["id"],)).fetchone()
                for t, c in children
            ):
                raise conflict(f"The {kind} is not empty")
            if kind == "table":
                db.execute("DELETE FROM maintenance_runs WHERE table_id=?", (record["id"],))
                db.execute("DELETE FROM table_policies WHERE table_id=?", (record["id"],))
            guard, extra = ("", []) if if_match is None else (" AND version=?", [if_match])
            deleted = db.execute(
                f"DELETE FROM {table} WHERE id=?{guard}", [record["id"], *extra]
            ).rowcount
            if not deleted:
                raise PreconditionFailedError("The resource changed; reload and retry")
            db.execute(
                "DELETE FROM grants WHERE securable_type=? AND securable_id=?", (kind, record["id"])
            )

    # ------------------------------------------------------------------ authorization
    @staticmethod
    def chain(kind: str, record: dict[str, Any]) -> list[Securable]:
        if kind == "catalog":
            return [Securable("catalog", record["id"], record["owner"])]
        if kind == "schema":
            return [
                Securable("catalog", record["catalog_id"], record["catalog_owner"]),
                Securable("schema", record["id"], record["owner"]),
            ]
        return [
            Securable("catalog", record["catalog_id"], record["catalog_owner"]),
            Securable("schema", record["schema_id"], record["schema_owner"]),
            Securable(kind, record["id"], record["owner"]),
        ]

    def privileges(self, principal: Principal, chain: list[Securable]) -> set[str]:
        """Effective privileges on the last securable: owners and admins hold all of them."""
        if principal.is_admin or any(s.owner in principal.identities for s in chain):
            return {ALL}
        identities = list(principal.identities)
        clause = " OR ".join("(securable_type=? AND securable_id=?)" for _ in chain)
        with self.connection() as db:
            rows = db.execute(
                f"SELECT securable_type, privilege FROM grants WHERE ({clause}) AND principal IN "
                f"({', '.join('?' * len(identities))})",
                [v for s in chain for v in (s.type, s.id)] + identities,
            ).fetchall()
        held = set()
        last = chain[-1].type
        for row in rows:
            privilege = row["privilege"]
            # USE_CATALOG / USE_SCHEMA apply only where granted; data privileges inherit down.
            if privilege == "USE_CATALOG" and row["securable_type"] != "catalog":
                continue
            if privilege == "USE_SCHEMA" and row["securable_type"] in {"table", "view"}:
                continue
            held.add(privilege)
        if last in {"table", "view"}:
            held.discard("CREATE_TABLE")
        return held

    def require(
        self, principal: Principal, kind: str, record: dict[str, Any], *needed: str
    ) -> None:
        """Unity-style checks: browse requires USE on every ancestor, plus the named privilege."""
        chain = self.chain(kind, record)
        for depth, securable in enumerate(chain):
            held = self.privileges(principal, chain[: depth + 1])
            if ALL in held:
                continue
            use = "USE_CATALOG" if securable.type == "catalog" else "USE_SCHEMA"
            if securable.type in {"catalog", "schema"} and use not in held:
                raise forbidden(f"Missing {use} on {securable.type}")
        held = self.privileges(principal, chain)
        if ALL in held:
            return
        missing = [p for p in needed if p not in held]
        if missing:
            raise forbidden(f"Missing {', '.join(missing)} on {kind}")

    def can_browse(self, principal: Principal, kind: str, record: dict[str, Any]) -> bool:
        """Catalogs and schemas need USE on the way down; tables and views also need some data
        privilege (owned, granted, or inherited), so objects a caller has no claim on stay hidden."""
        try:
            self.require(principal, kind, record)
        except LoamzError:
            return False
        if kind in {"catalog", "schema"}:
            return True
        held = self.privileges(principal, self.chain(kind, record))
        return bool(held & ({ALL} | PRIVILEGES[kind]))

    def can_use_compute(self, principal: Principal, resource_id: str) -> bool:
        if principal.is_admin:
            return True
        identities = list(principal.identities)
        with self.connection() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM grants WHERE securable_type='compute' AND securable_id=? AND "
                    f"privilege IN ('USE_COMPUTE', '{ALL}') AND principal IN "
                    f"({', '.join('?' * len(identities))}) LIMIT 1",
                    [resource_id, *identities],
                ).fetchone()
            )

    def grant(
        self, kind: str, securable_id: str, grantee: str, privilege: str, granted_by: str
    ) -> None:
        if privilege != ALL and privilege not in PRIVILEGES[kind]:
            raise InvalidRequestError(f"{privilege} does not apply to a {kind}")
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO grants VALUES (?,?,?,?,?,?)",
                (kind, securable_id, grantee, privilege, granted_by, now()),
            )

    def revoke(self, kind: str, securable_id: str, grantee: str, privilege: str) -> None:
        with self.connection() as db:
            db.execute(
                "DELETE FROM grants WHERE securable_type=? AND securable_id=? AND principal=? "
                "AND privilege=?",
                (kind, securable_id, grantee, privilege),
            )

    def grants(self, kind: str, securable_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT principal, privilege, granted_by, created_at FROM grants "
                    "WHERE securable_type=? AND securable_id=? ORDER BY principal, privilege",
                    (kind, securable_id),
                )
            ]

    # ------------------------------------------------------------------ audit
    AUDIT_FIELDS = (
        "at",
        "request_id",
        "principal",
        "actor",
        "action",
        "securable",
        "outcome",
        "detail",
    )

    @staticmethod
    def audit_hash(previous: str, row: Mapping[str, Any]) -> str:
        """Each event commits to the one before it: altering or removing any event breaks every
        hash after it, which `verify_audit` detects."""
        body = json.dumps([row[f] for f in CatalogStore.AUDIT_FIELDS], default=str)
        return hashlib.sha256((previous + "|" + body).encode()).hexdigest()

    def audit(
        self,
        principal: Principal | None,
        action: str,
        securable: str | None,
        outcome: str,
        request_id: str | None = None,
        **detail: Any,
    ) -> None:
        row = {
            "at": now(),
            "request_id": request_id,
            "principal": principal.id if principal else None,
            "actor": principal.actor if principal else None,
            "action": action,
            "securable": securable,
            "outcome": outcome,
            "detail": json.dumps(detail, default=str) if detail else None,
        }
        with self.connection() as db:
            self.backend.lock_audit(db)  # one writer at a time, so the chain cannot fork
            last = db.execute("SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
            previous = (last["hash"] if last else None) or ""
            db.execute(
                "INSERT INTO audit_events (at, request_id, principal, actor, action, securable, "
                "outcome, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*(row[f] for f in self.AUDIT_FIELDS), previous, self.audit_hash(previous, row)),
            )

    def verify_audit(self, limit: int = 1_000_000) -> dict[str, Any]:
        """Recompute the chain. Events older than the retention horizon are gone, so the check
        starts from the first remaining event's recorded predecessor."""
        checked, previous, first_bad = 0, None, None
        with self.connection() as db:
            rows = db.execute("SELECT * FROM audit_events ORDER BY id LIMIT ?", (limit,)).fetchall()
        for r in rows:
            if r["hash"] is None:  # written before chaining existed
                previous = None
                continue
            expected_prev = previous if previous is not None else (r["prev_hash"] or "")
            if (r["prev_hash"] or "") != expected_prev or r["hash"] != self.audit_hash(
                r["prev_hash"] or "", r
            ):
                first_bad = r["id"]
                break
            previous = r["hash"]
            checked += 1
        return {"ok": first_bad is None, "events_checked": checked, "first_bad_id": first_bad}

    def audit_since(self, after: int, limit: int) -> list[dict[str, Any]]:
        """Oldest first from just after `after`: what a log collector polls."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM audit_events WHERE id>? ORDER BY id LIMIT ?", (after, limit)
            ).fetchall()
        return [decode(r, ("detail",)) for r in rows]

    def purge_history(self, older_than_days: int) -> dict[str, int]:
        """Delete audit events and finished statements older than the retention period."""
        if older_than_days <= 0:
            return {"audit_events": 0, "statements": 0}
        cutoff = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
        with self.connection() as db:
            db.execute("DELETE FROM usage_daily WHERE day < ?", (cutoff[:10],))
            audit = db.execute("DELETE FROM audit_events WHERE at < ?", (cutoff,)).rowcount
            done = db.execute(
                "DELETE FROM statements WHERE created_at < ? "
                "AND status NOT IN ('queued','running')",
                (cutoff,),
            ).rowcount
        return {"audit_events": audit, "statements": done}

    def audit_events(self, before: int | None, limit: int) -> list[dict[str, Any]]:
        with self.connection() as db:
            where, params = ("", []) if before is None else (" WHERE id<?", [before])
            rows = db.execute(
                f"SELECT * FROM audit_events{where} ORDER BY id DESC LIMIT ?", [*params, limit]
            ).fetchall()
        return [decode(r, ("detail",)) for r in rows]

    # ------------------------------------------------------------------ usage
    def usage(self, principal: str, day: str | None = None) -> dict[str, int]:
        day = day or datetime.now(UTC).strftime("%Y-%m-%d")
        with self.connection() as db:
            row = db.execute(
                "SELECT statements, bytes_planned FROM usage_daily WHERE principal=? AND day=?",
                (principal, day),
            ).fetchone()
        return (
            {"statements": row["statements"], "bytes_planned": row["bytes_planned"]}
            if row
            else {
                "statements": 0,
                "bytes_planned": 0,
            }
        )

    def add_usage(self, principal: str, planned_bytes: int) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        with self.connection() as db:
            db.execute(
                "INSERT INTO usage_daily (principal, day, statements, bytes_planned) VALUES (?,?,1,?) "
                "ON CONFLICT (principal, day) DO UPDATE SET statements=usage_daily.statements+1, "
                "bytes_planned=usage_daily.bytes_planned+?",
                (principal, day, planned_bytes, planned_bytes),
            )

    # ------------------------------------------------------------------ access policies
    POLICY_JSON = ("column_masks", "exempt")

    def set_policy(self, table_id: str, policy: dict[str, Any], updated_by: str) -> dict[str, Any]:
        with self.connection() as db:
            existing = db.execute(
                "SELECT version FROM table_policies WHERE table_id=?", (table_id,)
            ).fetchone()
            values = [
                policy.get("row_filter"),
                json.dumps(policy.get("column_masks", [])),
                json.dumps(policy.get("exempt", [])),
                updated_by,
                now(),
            ]
            if existing:
                db.execute(
                    "UPDATE table_policies SET row_filter=?, column_masks=?, exempt=?, updated_by=?, "
                    "updated_at=?, version=version+1 WHERE table_id=?",
                    [*values, table_id],
                )
            else:
                db.execute(
                    "INSERT INTO table_policies (row_filter, column_masks, exempt, updated_by, "
                    "updated_at, table_id) VALUES (?,?,?,?,?,?)",
                    [*values, table_id],
                )
        return self.policy(table_id)

    def policy(self, table_id: str) -> dict[str, Any] | None:
        return self.policies([table_id]).get(table_id)

    def policies(self, table_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Policies of these tables in one query; tables without one are absent."""
        if not table_ids:
            return {}
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM table_policies WHERE table_id IN "
                f"({', '.join('?' * len(table_ids))})",
                table_ids,
            ).fetchall()
        return {r["table_id"]: decode(r, self.POLICY_JSON) for r in rows}

    def delete_policy(self, table_id: str) -> bool:
        with self.connection() as db:
            return bool(
                db.execute("DELETE FROM table_policies WHERE table_id=?", (table_id,)).rowcount
            )

    # ------------------------------------------------------------------ workload records
    def record(self, table: str, values: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as db:
            stamp = now()
            row = {"id": uuid4().hex, "created_at": stamp, **values}
            if table in {"imports", "pipelines"}:
                row.setdefault("updated_at", stamp)
            try:
                db.execute(
                    f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                    [json.dumps(v) if isinstance(v, (list, dict)) else v for v in row.values()],
                )
            except self.backend.integrity_errors:
                raise conflict("A record with this id already exists") from None
        return row

    def update(self, table: str, record_id: str, values: dict[str, Any]) -> None:
        if table in {"imports", "pipelines"}:
            values = {**values, "updated_at": now()}
        assignments = ", ".join(f"{k}=?" for k in values)
        with self.connection() as db:
            db.execute(
                f"UPDATE {table} SET {assignments} WHERE id=?",
                [
                    *(json.dumps(v) if isinstance(v, (list, dict)) else v for v in values.values()),
                    record_id,
                ],
            )

    def get(self, table: str, record_id: str, json_fields: tuple[str, ...] = ()) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise not_found(table.rstrip("s").replace("_", " ").capitalize())
        return decode(row, json_fields)

    def page(
        self,
        table: str,
        where: str,
        params: list[Any],
        before: str | None,
        limit: int,
        json_fields: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Newest-first cursor page keyed on (created_at, id)."""
        cursor = ""
        values = list(params)
        if before:
            created, _, record_id = before.partition("|")
            cursor = " AND (created_at<? OR (created_at=? AND id<?))"
            values += [created, created, record_id]
        with self.connection() as db:
            rows = db.execute(
                f"SELECT * FROM {table} WHERE {where}{cursor} ORDER BY created_at DESC, id DESC "
                "LIMIT ?",
                [*values, limit],
            ).fetchall()
        return [decode(r, json_fields) for r in rows]
