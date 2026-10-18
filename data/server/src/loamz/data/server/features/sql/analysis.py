"""Parse a statement once: enforce the read-only policy, find table references, and derive
per-reference filters that are safe for file pruning. The planner rewrites references to the
aliases a ticket defines, so workers never resolve names themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from loamz.data.server.kernel.errors import invalid

DIALECT = "duckdb"
# Functions that touch files, the environment, settings or extensions. Table functions are
# rejected separately (a table reference must be a plain name).
DENIED_FUNCTIONS = {
    "GETENV",
    "CURRENT_SETTING",
    "WHICH_SECRET",
    "READ_TEXT",
    "READ_BLOB",
    "GLOB",
    "QUERY",
    "QUERY_TABLE",
    "SNIFF_CSV",
}
DENIED_PREFIXES = ("READ_", "PARQUET_", "ICEBERG_", "DELTA_", "DUCKDB_", "PRAGMA_", "SQLITE_")
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "system", "temp"}
PRUNABLE = {
    exp.EQ: "eq",
    exp.NEQ: "ne",
    exp.GT: "gt",
    exp.GTE: "ge",
    exp.LT: "lt",
    exp.LTE: "le",
}
FLIPPED = {"gt": "lt", "ge": "le", "lt": "gt", "le": "ge", "eq": "eq", "ne": "ne"}


@dataclass
class TableRef:
    node: exp.Table
    name: tuple[str, str, str]
    alias: str
    filters: list[tuple[str, str, Any]] = field(default_factory=list)


@dataclass
class Analysis:
    tree: exp.Expression
    refs: list[TableRef]

    @property
    def names(self) -> list[tuple[str, str, str]]:
        return sorted({ref.name for ref in self.refs})

    def qualify(self) -> None:
        """Write every resolved name back into the tree, so the SQL means the same anywhere."""
        for ref in self.refs:
            ref.node.set("catalog", exp.to_identifier(ref.name[0]))
            ref.node.set("db", exp.to_identifier(ref.name[1]))

    def rewrite(self, aliases: dict[int, str], limit: int | None = None) -> str:
        """SQL with every catalog reference replaced by its ticket alias."""
        tree = self.tree.copy()
        originals = {id(ref.node): ref for ref in self.refs}
        for node, original in zip(
            tree.find_all(exp.Table), self.tree.find_all(exp.Table), strict=True
        ):
            ref = originals.get(id(original))
            if ref is None:
                continue
            node.replace(
                exp.Table(
                    this=exp.to_identifier(aliases[id(original)]),
                    alias=exp.TableAlias(this=exp.to_identifier(ref.alias)),
                )
            )
        sql = tree.sql(dialect=DIALECT)
        if limit is not None:
            sql = f"SELECT * FROM ({sql}) AS bounded LIMIT {int(limit)}"
        return sql


# Anything that can differ between two runs over the same files. Unknown (anonymous) functions are
# treated the same way: not cacheable is always safe.
NONDETERMINISTIC = {
    "RANDOM", "RAND", "UUID", "UUID_STRING", "GEN_RANDOM_UUID", "UUIDV4", "UUIDV7", "NOW",
    "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME", "CURRENT_DATETIME", "TODAY",
    "LOCALTIMESTAMP", "LOCALTIME", "TRANSACTION_TIMESTAMP", "GET_CURRENT_TIMESTAMP",
    "GET_CURRENT_TIME", "TIMEOFDAY", "SETSEED", "NEXTVAL", "CURRVAL", "CURRENT_USER",
    "SESSION_USER", "USER", "CURRENT_SCHEMA", "CURRENT_DATABASE", "CURRENT_CATALOG", "VERSION",
    "CURRENT_SETTING", "AGE",
}  # fmt: skip


def cacheable(tree: exp.Expression) -> bool:
    """Whether the same SQL over the same files always yields the same rows."""
    for node in tree.walk():
        if isinstance(node, exp.Func) and (
            isinstance(node, exp.Anonymous) or _function_name(node) in NONDETERMINISTIC
        ):
            return False
        if isinstance(node, (exp.CurrentUser, exp.Rand)):
            return False
    return True


def _function_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).upper()


def _validate(tree: exp.Expression) -> None:
    if not isinstance(tree, exp.Query):
        raise invalid("Only one read-only SELECT or WITH query is supported")
    for node in tree.walk():
        if isinstance(
            node,
            (
                exp.Command,
                exp.Insert,
                exp.Delete,
                exp.Update,
                exp.Create,
                exp.Drop,
                exp.Into,
                exp.Merge,
                exp.Copy,
                exp.Pragma,
                exp.Set,
                exp.Use,
            ),
        ):
            raise invalid("Statements may only read data")
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            raise invalid("Table functions and direct file access are disabled; use catalog tables")
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name in DENIED_FUNCTIONS or name.startswith(DENIED_PREFIXES):
                raise invalid(f"Function {name} is not allowed")


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def _literal(node: exp.Expression) -> Any:
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
        value = _literal(node.this)
        return -value if isinstance(value, (int, float)) else None
    if isinstance(node, exp.Cast):
        return _literal(node.this)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        text = node.this
        return float(text) if any(c in text for c in ".eE") else int(text)
    if isinstance(node, exp.Boolean):
        return node.this
    return None


def _atoms(where: exp.Expression) -> list[exp.Expression]:
    """Top-level AND conjuncts."""
    if isinstance(where, exp.And):
        return _atoms(where.left) + _atoms(where.right)
    if isinstance(where, exp.Paren):
        return _atoms(where.this)
    return [where]


def _pushdown(select: exp.Select, refs: list[TableRef]) -> None:
    """Attach null-rejecting `column op literal` conjuncts to the reference they constrain.

    Only for selects without outer joins: pruning the nullable side of an outer join could
    change which rows are NULL-extended.
    """
    if any(j.side for j in select.args.get("joins") or []):
        return
    where = select.args.get("where")
    if where is None or not refs:
        return
    by_alias = {ref.alias.lower(): ref for ref in refs}
    single = refs[0] if len(refs) == 1 else None

    def owner(column: exp.Column) -> TableRef | None:
        if column.table:
            return by_alias.get(column.table.lower())
        return single

    for atom in _atoms(where.this):
        operator = PRUNABLE.get(type(atom))
        if operator:
            left, right = atom.left, atom.right
            if isinstance(right, exp.Column) and not isinstance(left, exp.Column):
                left, right, operator = right, left, FLIPPED[operator]
            value = _literal(right)
            if isinstance(left, exp.Column) and value is not None and (ref := owner(left)):
                ref.filters.append((left.name, operator, value))
        elif (
            isinstance(atom, exp.In)
            and isinstance(atom.this, exp.Column)
            and not atom.args.get("query")
        ):
            values = [_literal(v) for v in atom.expressions]
            if values and None not in values and (ref := owner(atom.this)):
                ref.filters.append((atom.this.name, "in", values))
        elif isinstance(atom, exp.Between) and isinstance(atom.this, exp.Column):
            low, high = _literal(atom.args["low"]), _literal(atom.args["high"])
            if low is not None and high is not None and (ref := owner(atom.this)):
                ref.filters += [(atom.this.name, "ge", low), (atom.this.name, "le", high)]


def analyze(sql: str, default_catalog: str, default_schema: str) -> Analysis:
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise invalid(f"SQL syntax error: {str(exc).splitlines()[0][:300]}") from None
    if len(statements) != 1:
        raise invalid("Submit exactly one statement")
    tree = statements[0]
    _validate(tree)
    ctes = _cte_names(tree)
    refs: list[TableRef] = []
    by_node: dict[int, TableRef] = {}
    for node in tree.find_all(exp.Table):
        parts = [p.name for p in (node.args.get("catalog"), node.args.get("db"), node.this) if p]
        if len(parts) == 1 and parts[0].lower() in ctes:
            continue
        if any(p.lower() in SYSTEM_SCHEMAS for p in parts[:-1]) or parts[-1].lower().startswith(
            ("duckdb_", "pg_", "sqlite_")
        ):
            raise invalid("System catalogs are not available; use the catalog API")
        if len(parts) == 1:
            name = (default_catalog, default_schema, parts[0])
        elif len(parts) == 2:
            name = (default_catalog, parts[0], parts[1])
        else:
            name = (parts[0], parts[1], parts[2])
        ref = TableRef(node=node, name=tuple(p.lower() for p in name), alias=node.alias_or_name)
        refs.append(ref)
        by_node[id(node)] = ref
    for select in tree.find_all(exp.Select):
        scope = [
            by_node[id(t)]
            for source in [
                select.args.get("from_") or select.args.get("from"),
                *(select.args.get("joins") or []),
            ]
            if source is not None
            for t in [source.this]
            if isinstance(t, exp.Table) and id(t) in by_node
        ]
        _pushdown(select, scope)
    return Analysis(tree=tree, refs=refs)


def incremental_source(sql: str, default_catalog: str, default_schema: str) -> tuple[str, str, str]:
    """The single table an incrementally refreshable definition reads, or raise.

    Only a plain projection/filter of one table qualifies: each output row then depends on exactly one
    input row, so processing only new input files and appending gives the same table as recomputing.
    Aggregates, DISTINCT, joins, windows, LIMIT, ORDER BY, set operations, CTEs and subqueries do not.
    """
    analysis = analyze(sql, default_catalog, default_schema)
    tree = analysis.tree
    select = tree if isinstance(tree, exp.Select) else None
    reasons = []
    if select is None:
        reasons.append("it is not a single SELECT")
    else:
        for key, why in (
            ("group", "it groups rows"),
            ("having", "it has HAVING"),
            ("distinct", "it uses DISTINCT"),
            ("limit", "it has LIMIT"),
            ("order", "it has ORDER BY"),
            ("joins", "it joins tables"),
            ("with_", "it uses a WITH clause"),
            ("with", "it uses a WITH clause"),
            ("qualify", "it uses QUALIFY"),
        ):
            if select.args.get(key):
                reasons.append(why)
        if any(tree.find_all(exp.Window, exp.AggFunc, exp.Subquery)):
            reasons.append("it uses aggregates, windows or subqueries")
    if len(analysis.refs) != 1:
        reasons.append("it must read exactly one table")
    if not cacheable(tree):
        reasons.append("it uses a function whose value changes between runs")
    if reasons:
        raise invalid(
            "Incremental refresh needs a plain filter/projection of one table: "
            + "; ".join(sorted(set(reasons)))
        )
    return analysis.refs[0].name
