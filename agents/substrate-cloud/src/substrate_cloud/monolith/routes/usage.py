"""``GET /me/usage``: what the caller has used, by day, so a limit or a bill is never a surprise."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.services.thread_service import owned_thread_ids
from substrate_cloud.stream.usage import usage_by_day

router = APIRouter(prefix="/me/usage", tags=["usage"])


class DayOut(BaseModel):
    date: date
    messages: int
    calls: int
    tokens: int
    cost_usd: float


class Totals(BaseModel):
    messages: int
    tokens: int
    cost_usd: float


class UsageOut(BaseModel):
    days: list[DayOut]
    today: Totals
    this_month: Totals
    window: Totals
    """Everything in ``days``."""
    note: str


def _sum(days: list[DayOut]) -> Totals:
    return Totals(
        messages=sum(d.messages for d in days),
        tokens=sum(d.tokens for d in days),
        cost_usd=round(sum(d.cost_usd for d in days), 6),
    )


@router.get("", response_model=UsageOut)
async def get_usage(
    request: Request,
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> UsageOut:
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    store = getattr(request.app.state, "store", None)
    thread_ids = [str(i) for i in await owned_thread_ids(db, user_identifier=user.sub)]
    series = (
        await usage_by_day(
            store, tenant=user.tenant_id, thread_ids=thread_ids, since=since
        )
        if store is not None
        else []
    )
    out = [
        DayOut(
            date=d.day,
            messages=d.messages,
            calls=d.calls,
            tokens=d.tokens,
            cost_usd=round(d.cost_usd, 6),
        )
        for d in series
    ]
    today = now.date()
    return UsageOut(
        days=out,
        today=_sum([d for d in out if d.date == today]),
        this_month=_sum(
            [
                d
                for d in out
                if d.date.year == today.year and d.date.month == today.month
            ]
        ),
        window=_sum(out),
        note="Counts every model call in your conversations, scheduled tasks included. UTC days.",
    )
