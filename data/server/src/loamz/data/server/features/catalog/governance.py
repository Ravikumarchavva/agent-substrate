"""/api/v1/me, /overview and the audit log (who is asking, what they can see, and what happened)."""

from __future__ import annotations

from typing import Literal

import orjson
from fastapi import APIRouter, Query, Response

from loamz.data.server.kernel.errors import forbidden
from loamz.data.server.kernel.http import Caller, Svc

router = APIRouter(prefix="/api/v1")
FULL_NAME = r"^[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}$"


# ------------------------------------------------------------------ identity
@router.get("/me", tags=["identity"])
def me(caller: Caller):
    return {
        "principal": caller.id,
        "name": caller.name,
        "kind": caller.kind,
        "email": caller.email,
        "groups": sorted(caller.groups),
        "is_admin": caller.is_admin,
        "actor": caller.actor,
    }


@router.get("/overview", tags=["identity"])
def overview(svc: Svc, caller: Caller):
    with svc.store.connection() as db:
        rows = db.execute(
            svc.store.TABLE_SELECT + "ORDER BY t.updated_at DESC LIMIT 500"
        ).fetchall()
    from loamz.data.server.kernel.metadata import TABLE_JSON, decode

    tables = [decode(r, TABLE_JSON) for r in rows]
    visible = [t for t in tables if svc.store.can_browse(caller, "table", t)]
    recent = svc.store.page(
        "statements", "principal=?", [caller.id], None, 8, ("tables", "context")
    )
    return {
        "tables": len(visible),
        "rows": sum(t["row_count"] for t in visible),
        "size_bytes": sum(t["size_bytes"] for t in visible),
        "recent_tables": [
            {
                "full_name": f"{t['catalog_name']}.{t['schema_name']}.{t['name']}",
                "row_count": t["row_count"],
                "updated_at": t["updated_at"],
            }
            for t in visible[:8]
        ],
        "recent_statements": recent,
    }


@router.get("/audit-events", tags=["identity"])
def audit_events(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(100, ge=1, le=1000),
):
    if not caller.is_admin:
        raise forbidden("Only admins can read the audit log")
    before = int(page_token) if page_token and page_token.isdigit() else None
    rows = svc.store.audit_events(before, page_size + 1)
    more = len(rows) > page_size
    rows = rows[:page_size]
    return {"events": rows, "next_page_token": str(rows[-1]["id"]) if more else None}


def cef(event: dict) -> str:
    """ArcSight Common Event Format, understood by most SIEMs."""

    def esc(value: object, header: bool = False) -> str:
        text = str(value if value is not None else "")
        text = text.replace("\\", "\\\\").replace(
            "|" if header else "=", "\\|" if header else "\\="
        )
        return text.replace("\n", " ")

    severity = 7 if event["outcome"] != "success" else 3
    extension = (
        f"rt={esc(event['at'])} suser={esc(event['principal'])} "
        f"duser={esc(event['actor'])} cs1Label=securable cs1={esc(event['securable'])} "
        f"outcome={esc(event['outcome'])} externalId={event['id']} "
        f"cs2Label=hash cs2={esc(event.get('hash'))}"
    )
    return f"CEF:0|Loamz|Data|1|{esc(event['action'], True)}|{esc(event['action'], True)}|{severity}|{extension}"


@router.get("/audit-events:export", tags=["identity"])
def export_audit_events(
    svc: Svc,
    caller: Caller,
    after: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
    format: Literal["ndjson", "cef"] = "ndjson",
):
    """For log collectors: oldest first, resumable. Pass the last `id` you received as `after`.
    The `X-Next-After` header carries it; an empty body means you are caught up."""
    if not caller.is_admin:
        raise forbidden("Only admins can read the audit log")
    rows = svc.store.audit_since(after, limit)
    if format == "cef":
        body = "".join(cef(r) + "\n" for r in rows)
        media = "text/plain"
    else:
        body = "".join(orjson.dumps(r).decode() + "\n" for r in rows)
        media = "application/x-ndjson"
    return Response(
        body,
        media_type=media,
        headers={"X-Next-After": str(rows[-1]["id"] if rows else after)},
    )


@router.get("/audit-events:verify", tags=["identity"])
def verify_audit_events(svc: Svc, caller: Caller):
    """Check that no audit event has been altered or removed from the middle of the log."""
    if not caller.is_admin:
        raise forbidden("Only admins can read the audit log")
    return svc.store.verify_audit()
