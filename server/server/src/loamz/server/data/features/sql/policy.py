"""Row filters and column masks: access rules a table carries, applied by the planner.

A policy turns the table a reader names into a *subquery over the table*: masked columns are
replaced by their masked expression and the row filter becomes a WHERE. Everything the reader
writes (filters, joins, grouping, ordering) then runs on the masked output, so a mask cannot be
bypassed by predicating on the hidden value. The rewrite happens in the planner before the
statement is signed, so a worker only ever sees SQL that already contains the rules.

Policy expressions are a small, safe language: columns of this table, literals, comparison and
arithmetic, CASE, CAST, and a short list of scalar functions. No subqueries, no table access, no
unknown functions. Three context functions are resolved to literals for the reading principal:

    current_user()      the principal's name        is_member('group')  true if they belong to it
    current_tenant()    the agent run's tenant, else NULL
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.simplify import simplify

from loamz.engine.errors import ConflictError
from loamz.server.data.features.sql.analysis import DIALECT
from loamz.server.data.kernel.errors import invalid
from loamz.server.data.kernel.principal import Principal

MAX_EXPRESSION = 2000
CONTEXT_FUNCTIONS = {"IS_MEMBER", "CURRENT_TENANT", "CURRENT_PRINCIPAL", "CURRENT_USER"}
ALLOWED_FUNCTIONS = {
    "UPPER", "LOWER", "LENGTH", "SUBSTR", "SUBSTRING", "LEFT", "RIGHT", "CONCAT", "TRIM", "LTRIM",
    "RTRIM", "REPLACE", "COALESCE", "NULLIF", "IF", "IFNULL", "ROUND", "FLOOR", "CEIL", "CEILING",
    "ABS", "MD5", "SHA256", "DATE_TRUNC", "YEAR", "MONTH", "DAY", "HOUR", "STRFTIME", "LPAD",
    "RPAD", "REGEXP_REPLACE", "REGEXP_MATCHES", "STARTS_WITH", "CONTAINS", "ENDS_WITH", "LEAST",
    "GREATEST", "TRY_CAST", "CAST", "EXTRACT", "DATE_PART", "NOW", "CURRENT_DATE", "TODAY",
}  # fmt: skip
FORBIDDEN_NODES = (
    exp.Select, exp.Subquery, exp.Exists, exp.Table, exp.Window, exp.Lambda, exp.Placeholder,
    exp.Parameter, exp.Star, exp.Command, exp.Query,
)  # fmt: skip
# sqlglot models some operators and constructs as "functions"; they are not function calls.
OPERATORS = (
    exp.Binary, exp.Unary, exp.Connector, exp.Paren, exp.Case, exp.If, exp.Cast, exp.TryCast,
    exp.In, exp.Between, exp.Is, exp.Not, exp.Neg, exp.Coalesce, exp.Nullif, exp.Concat,
)  # fmt: skip
MASKS = {"null", "redact", "hash", "expression"}


def parse(text: str) -> exp.Expression:
    if not text or len(text) > MAX_EXPRESSION:
        raise invalid(f"Policy expressions must be 1 to {MAX_EXPRESSION} characters")
    try:
        return sqlglot.parse_one(text, read=DIALECT)
    except sqlglot.errors.ParseError as exc:
        raise invalid(f"Policy expression error: {str(exc).splitlines()[0][:200]}") from None


def _name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).upper()


def validate_expression(tree: exp.Expression, columns: set[str]) -> None:
    """Raise unless `tree` only uses this table's columns and the allowed scalar functions."""
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise invalid("Policy expressions cannot contain queries or table access")
        if isinstance(node, exp.Column):
            if node.table:
                raise invalid("Refer to columns by name only, without a table prefix")
            if node.name.lower() not in columns:
                raise invalid(f"Unknown column {node.name!r} in policy expression")
        elif isinstance(node, exp.Func) and not isinstance(node, OPERATORS):
            name = _name(node)
            if name not in ALLOWED_FUNCTIONS and name not in CONTEXT_FUNCTIONS:
                raise invalid(f"Function {name} is not allowed in a policy")


def resolve_context(tree: exp.Expression, subject: Principal) -> exp.Expression:
    """Replace the context functions by literals for this reader. The result is plain SQL."""
    tree = tree.copy()

    def literal(value: str | None) -> exp.Expression:
        return exp.Null() if value is None else exp.Literal.string(value)

    for node in list(tree.find_all(exp.Func)) + list(tree.find_all(exp.CurrentUser)):
        if isinstance(node, exp.CurrentUser):
            node.replace(literal(subject.name))
            continue
        name = _name(node)
        if name == "IS_MEMBER":
            groups = {f"group:{g}" for g in subject.groups} | set(subject.identities)
            asked = node.expressions[0] if node.expressions else None
            if not isinstance(asked, exp.Literal) or not asked.is_string:
                raise invalid("is_member() takes a group name in quotes")
            member = asked.this in subject.groups or f"group:{asked.this}" in groups
            node.replace(exp.Boolean(this=member))
        elif name == "CURRENT_TENANT":
            node.replace(literal(subject.context.get("tenant_id")))
        elif name == "CURRENT_PRINCIPAL":
            node.replace(literal(subject.id))
        elif name == "CURRENT_USER":
            node.replace(literal(subject.name))
    # With the reader's facts filled in, `(TRUE AND region='EU') OR (FALSE AND ...)` collapses to
    # `region = 'EU'`, which the planner can use to skip files.
    return simplify(tree, dialect=DIALECT)


def validate_policy(policy: dict[str, Any], table_columns: list[dict[str, Any]], salt: str) -> None:
    columns = {c["name"].lower() for c in table_columns}
    if policy.get("row_filter"):
        validate_expression(parse(policy["row_filter"]), columns)
    seen: set[str] = set()
    for mask in policy.get("column_masks", []):
        name = mask["column"].lower()
        if name not in columns:
            raise invalid(f"Cannot mask unknown column {mask['column']!r}")
        if name in seen:
            raise invalid(f"Column {mask['column']!r} is masked twice")
        seen.add(name)
        if mask["mask"] not in MASKS:
            raise invalid(f"Unknown mask {mask['mask']!r}; use one of {sorted(MASKS)}")
        if mask["mask"] == "hash" and not salt:
            raise ConflictError(
                "Hash masks need a secret salt: set LOAMZ_DATA_MASK_SALT (unsalted hashes of "
                "low-entropy values can be reversed by guessing)"
            )
        if mask["mask"] == "expression":
            if not mask.get("expression"):
                raise invalid("An expression mask needs `expression`")
            validate_expression(parse(mask["expression"]), columns)


def applies(policy: dict[str, Any] | None, subject: Principal) -> bool:
    if not policy or not (policy.get("row_filter") or policy.get("column_masks")):
        return False
    return not (set(policy.get("exempt", [])) & set(subject.identities))


def _ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def masked_column(column: dict[str, Any], mask: dict[str, Any], salt: str) -> exp.Expression:
    kind = column["type"]
    ref = exp.Column(this=_ident(column["name"]))
    if mask["mask"] == "null" or (mask["mask"] == "redact" and kind != "VARCHAR"):
        value: exp.Expression = sqlglot.parse_one(f"CAST(NULL AS {kind})", read=DIALECT)
    elif mask["mask"] == "redact":
        value = exp.Literal.string("***")
    elif mask["mask"] == "hash":
        salted = exp.Concat(
            expressions=[
                exp.Literal.string(salt),
                exp.Cast(this=ref.copy(), to=exp.DataType.build("VARCHAR")),
            ]
        )
        value = exp.Case(
            ifs=[exp.If(this=exp.Is(this=ref.copy(), expression=exp.Null()), true=exp.Null())],
            default=exp.Anonymous(this="sha256", expressions=[salted]),
        )
    else:
        inner = parse(mask["expression"])
        value = sqlglot.parse_one(f"CAST(NULL AS {kind})", read=DIALECT)
        value.set("this", exp.Paren(this=inner))
    return exp.Alias(this=value, alias=_ident(column["name"]))


def wrap(
    policy: dict[str, Any],
    table: dict[str, Any],
    name: tuple[str, str, str],
    subject: Principal,
    alias: str,
    pushable: list[tuple[str, str, Any]],
    salt: str,
) -> exp.Subquery:
    """`(SELECT <masked columns> FROM table WHERE <row filter>) AS alias` for this reader.

    `pushable` are comparisons the reader already wrote in their WHERE; the ones on unmasked
    columns are repeated inside, where the planner can use them to skip files. They are redundant
    for the result and never touch a masked column (whose stored values differ from what is seen).
    """
    masks = {m["column"].lower(): m for m in policy.get("column_masks", [])}
    select = [
        masked_column(c, masks[c["name"].lower()], salt)
        if c["name"].lower() in masks
        else exp.Column(this=_ident(c["name"]))
        for c in table["columns"]
    ]
    source = exp.Table(
        this=exp.to_identifier(name[2]),
        db=exp.to_identifier(name[1]),
        catalog=exp.to_identifier(name[0]),
    )
    query = exp.select(*select).from_(source)
    conditions: list[exp.Expression] = []
    if policy.get("row_filter"):
        conditions.append(exp.Paren(this=resolve_context(parse(policy["row_filter"]), subject)))
    operators = {
        "eq": exp.EQ,
        "ne": exp.NEQ,
        "gt": exp.GT,
        "ge": exp.GTE,
        "lt": exp.LT,
        "le": exp.LTE,
    }
    for column, operator, value in pushable:
        if column.lower() in masks or operator not in operators and operator != "in":
            continue
        field = next((c for c in table["columns"] if c["name"].lower() == column.lower()), None)
        if field is None:
            continue
        literal = (
            exp.Literal.string(str(value))
            if isinstance(value, str)
            else exp.Boolean(this=value)
            if isinstance(value, bool)
            else exp.Literal.number(value)
        )
        column_ref = exp.Column(this=_ident(field["name"]))
        if operator == "in":
            items = [
                exp.Literal.string(str(v)) if isinstance(v, str) else exp.Literal.number(v)
                for v in value
            ]
            conditions.append(exp.In(this=column_ref, expressions=items))
        else:
            conditions.append(operators[operator](this=column_ref, expression=literal))
    if conditions:
        query = query.where(exp.and_(*conditions))
    return exp.Subquery(this=query, alias=exp.TableAlias(this=exp.to_identifier(alias)))
