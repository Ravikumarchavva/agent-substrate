from __future__ import annotations

from datetime import date

from pydantic import BaseModel


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
    """Everything in `days`."""
    note: str
