"""Authorize and plan a statement, then sign the ticket a worker will execute.

Views are expanded into subqueries before planning. Reading a view needs `SELECT` on the view; what
the view reads is authorized as the view's owner (definer rights, as in Unity Catalog), so a view can
share a filtered or aggregated slice of a table its readers cannot query directly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sqlglot import exp

from loamz.engine.errors import ConflictError, ForbiddenError, InvalidRequestError, LoamzError
from loamz.server.data.features.sql.analysis import DIALECT, analyze, cacheable
from loamz.server.data.features.sql.policy import applies as policy_applies
from loamz.server.data.features.sql.policy import wrap as wrap_policy
from loamz.server.data.features.tables.credentials import CredentialVendor, table_prefix
from loamz.server.data.features.tables.iceberg import TableService, table_service
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import invalid, not_found
from loamz.server.data.kernel.metadata import CatalogStore
from loamz.server.data.kernel.principal import Principal
from loamz.server.data.kernel.tickets import TicketSigner

MAX_VIEW_DEPTH = 8


@dataclass
class PlannedStatement:
    statement_id: str
    resource_id: str
    ticket: str
    sql: str
    tables: list[str]
    files: int
    bytes: int
    # Base tables the statement reads, with the snapshot of each at planning time.
    sources: dict[str, str | None] = field(default_factory=dict)


def definer_of(view: dict[str, Any]) -> Principal:
    """The identity a view reads with: its creator as they were when the view was defined."""
    info = view["definer"]
    kind, _, name = view["owner"].partition(":")
    return Principal(
        subject=info.get("subject", view["owner"]),
        name=name,
        kind="service" if kind == "service" else "user",
        groups=frozenset(info.get("groups", [])),
        is_admin=bool(info.get("is_admin")),
    )


class Planner:
    def __init__(
        self,
        store: CatalogStore,
        tables: TableService,
        signer: TicketSigner,
        mask_salt: str = "",
        vendor: CredentialVendor | None = None,
    ):
        self.store, self.tables, self.signer, self.mask_salt = store, tables, signer, mask_salt
        self.vendor = vendor

    def expand(
        self,
        sql: str,
        catalog: str,
        schema: str,
        subject: Principal,
        *,
        depth: int = 0,
        trail: tuple[tuple[str, str, str], ...] = (),
        checks: list[tuple[Principal, str, dict[str, Any]]],
        via_view: str | None = None,
    ) -> exp.Expression:
        """Parse `sql`, replace view references by their definitions, and record who must be
        allowed to read what. Table references inside the result are fully qualified."""
        analysis = analyze(sql, catalog, schema)
        names = analysis.names
        tables = self.store.tables_by_names(names)
        views = self.store.views_by_names([n for n in names if n not in tables])
        policies = self.store.policies([t["id"] for t in tables.values()])
        for ref in analysis.refs:
            if ref.name in views:
                view = views[ref.name]
                if ref.name in trail or depth >= MAX_VIEW_DEPTH:
                    raise invalid("Views are nested too deeply or refer to themselves")
                checks.append((subject, "view", view))
                inner = self.expand(
                    view["sql"],
                    view["catalog_name"],
                    view["schema_name"],
                    definer_of(view),
                    depth=depth + 1,
                    trail=(*trail, ref.name),
                    checks=checks,
                    via_view=".".join(ref.name),
                )
                ref.node.replace(
                    exp.Subquery(
                        this=inner, alias=exp.TableAlias(this=exp.to_identifier(ref.alias))
                    )
                )
            elif ref.name in tables:
                record = tables[ref.name]
                checks.append((subject, "table", record))
                policy = policies.get(record["id"])
                if policy_applies(policy, subject):
                    # Fail closed: a table with a policy is never read without it.
                    if not record["columns"]:
                        raise RuntimeError("A protected table has no known columns")
                    ref.node.replace(
                        wrap_policy(
                            policy,
                            record,
                            ref.name,
                            subject,
                            ref.alias,
                            ref.filters,
                            self.mask_salt,
                        )
                    )
            else:
                if via_view:
                    raise ConflictError(
                        f"View {via_view} refers to {'.'.join(ref.name)}, which no longer exists"
                    )
                raise not_found(f"Table {'.'.join(ref.name)}")
        analysis.qualify()
        return analysis.tree

    def authorize(self, checks: list[tuple[Principal, str, dict[str, Any]]], caller: Principal):
        for subject, kind, record in checks:
            name = f"{record['catalog_name']}.{record['schema_name']}.{record['name']}"
            if subject is caller:
                # Same answer for "absent" and "not visible": existence is not disclosed.
                if not self.store.can_browse(subject, kind, record):
                    raise not_found(f"{kind.capitalize()} {name}")
                self.store.require(subject, kind, record, "SELECT")
            else:
                try:
                    self.store.require(subject, kind, record, "SELECT")
                except LoamzError:
                    raise ForbiddenError(
                        "A view in this query reads data its owner can no longer access"
                    ) from None

    def plan(
        self,
        principal: Principal,
        sql: str,
        *,
        catalog: str,
        schema: str,
        max_rows: int | None,
        max_bytes: int,
        timeout_seconds: float,
        resource_id: str | Callable[[str], str],
        statement_id: str | None = None,
        op: str = "query",
        ttl: int | None = None,
        cache: bool = True,
        restrict: dict[str, set[str]] | None = None,
    ) -> PlannedStatement:
        checks: list[tuple[Principal, str, dict[str, Any]]] = []
        tree = self.expand(sql, catalog, schema, principal, checks=checks)
        self.authorize(checks, principal)
        expanded = tree.sql(dialect=DIALECT)
        analysis = analyze(expanded, catalog, schema)
        records = self.store.tables_by_names(analysis.names)
        allowed = {
            (r["catalog_name"], r["schema_name"], r["name"]) for _, k, r in checks if k == "table"
        }
        if not set(analysis.names) <= allowed or not set(analysis.names) <= set(records):
            raise RuntimeError("Planner lost track of a table reference")
        aliases: dict[int, str] = {}
        ticket_tables: list[dict[str, Any]] = []
        sources = [".".join(ref.name) for ref in analysis.refs]
        files = size = 0
        for index, ref in enumerate(analysis.refs):
            alias = f"__t{index}"
            aliases[id(ref.node)] = alias
            plan = self.tables.plan(records[ref.name], ref.filters)
            wanted = (restrict or {}).get(".".join(ref.name))
            if wanted is not None:  # e.g. an incremental refresh reads only the files added since
                plan = replace(plan, files=tuple(f for f in plan.files if f.path in wanted))
            files += len(plan.files)
            size += sum(f.size for f in plan.files)
            ticket_tables.append(
                {
                    "alias": alias,
                    "table": ".".join(ref.name),
                    "snapshot": str(plan.snapshot_id) if plan.snapshot_id else None,
                    "files": [f.path for f in plan.files],
                    "columns": [list(c) for c in plan.columns],
                }
            )
        if callable(resource_id):
            # Same tables, same worker: its warm file, metadata and result caches get reused.
            resource_id = resource_id(hashlib.sha1("|".join(sorted(sources)).encode()).hexdigest())
        rewritten = analysis.rewrite(aliases, None if max_rows is None else max_rows + 1)
        if len(rewritten) > 200_000:
            raise InvalidRequestError("Statement is too large")
        # Same SQL over the same immutable files gives the same rows: a worker may remember the answer.
        cache_key = None
        if cache and op == "query" and cacheable(analysis.tree):
            digest = hashlib.sha256(rewritten.encode())
            digest.update(
                json.dumps([[t["alias"], t["columns"], t["files"]] for t in ticket_tables]).encode()
            )
            cache_key = digest.hexdigest()
        storage = None
        if self.vendor and op == "query":
            prefixes = [p for t in ticket_tables if (p := table_prefix(t["files"]))]
            vended = self.vendor.for_prefixes(prefixes)
            storage = vended.claim() if vended else None
        ticket, statement_id = self.signer.sign(
            {
                "op": op,
                "cache": cache_key,
                "storage": storage,
                "sub": principal.id,
                "act": principal.actor,
                "resource": resource_id,
                "sql": rewritten,
                "tables": ticket_tables,
                "limits": {
                    "max_rows": None if max_rows is None else max_rows + 1,
                    "max_bytes": max_bytes,
                    "timeout_ms": int(timeout_seconds * 1000),
                },
            },
            statement_id,
            ttl,
        )
        referenced = sorted(
            {
                f"{r['catalog_name']}.{r['schema_name']}.{r['name']}"
                for _, k, r in checks
                if k == "view"
            }
            | {".".join(n) for n in analysis.names}
        )
        return PlannedStatement(
            statement_id=statement_id,
            resource_id=resource_id,
            ticket=ticket,
            sql=rewritten,
            tables=referenced,
            files=files,
            bytes=size,
            sources={t["table"]: t["snapshot"] for t in ticket_tables},
        )

    def cancel_ticket(self, principal: Principal, statement_id: str, resource_id: str) -> str:
        ticket, _ = self.signer.sign(
            {
                "op": "cancel",
                "sub": principal.id,
                "statement": statement_id,
                "resource": resource_id,
            }
        )
        return ticket


def statement_planner(container: Container) -> Planner:
    """Provider: authorizes, plans and signs statements."""
    settings = container.settings
    return Planner(
        container.store,
        container.get(table_service),
        container.signer,
        settings.mask_salt.get_secret_value(),
        CredentialVendor(settings) if settings.storage_sts_role_arn else None,
    )
