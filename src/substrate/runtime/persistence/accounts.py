"""Budget accounts over the runtime's SQL tables (see ``accounts.py``)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from substrate.runtime.store import Spend
from substrate.stores.database import Tx
from substrate.types.run_status import RunId
from substrate.types.supervision import ExecutionBudget


def _micros(usd: float | None) -> int | None:
    return None if usd is None else round(usd * 1_000_000)


async def exhausted(
    tx: Tx, accounts: Sequence[str], *, strict: bool = False
) -> str | None:
    """The first of ``accounts`` at (``strict``: past) a limit. Shared with channel wakes, which ask
    inside their own transaction."""
    cmp = ">" if strict else ">="
    for account in accounts:
        row = await tx.fetchone(
            "SELECT 1 AS x FROM rt_accounts WHERE account = ? AND ("
            f"(max_tokens IS NOT NULL AND tokens {cmp} max_tokens) OR "
            f"(max_cost_micros IS NOT NULL AND cost_micros {cmp} max_cost_micros) OR "
            f"(max_turns IS NOT NULL AND turns {cmp} max_turns))",
            account,
        )
        if row:
            return account
    return None


class Accounts:
    _tx: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]]

    async def account_limit(self, account: str, budget: ExecutionBudget) -> None:
        await self._tx(
            lambda tx: tx.execute(
                "INSERT INTO rt_accounts (account, max_tokens, max_cost_micros, max_turns) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (account) DO UPDATE SET max_tokens = EXCLUDED.max_tokens, "
                "max_cost_micros = EXCLUDED.max_cost_micros, max_turns = EXCLUDED.max_turns",
                account,
                budget.max_tokens,
                _micros(budget.max_cost_usd),
                budget.max_turns,
            )
        )

    async def account_spend(self, account: str) -> Spend:
        async def do(tx: Tx) -> Spend:
            row = await tx.fetchone(
                "SELECT tokens, cost_micros, turns FROM rt_accounts WHERE account = ?",
                account,
            )
            if row is None:
                return Spend()
            return Spend(
                tokens=int(row["tokens"]),
                cost_usd=int(row["cost_micros"]) / 1_000_000,
                turns=int(row["turns"]),
            )

        return await self._tx(do)

    async def accounts_exhausted(
        self, accounts: Sequence[str], *, strict: bool = False
    ) -> str | None:
        return await self._tx(lambda tx: exhausted(tx, accounts, strict=strict))

    async def run_accounts_exhausted(
        self, run_id: RunId, *, strict: bool = False
    ) -> str | None:
        async def do(tx: Tx) -> str | None:
            rows = await tx.fetchall(
                "SELECT account FROM rt_run_accounts WHERE run_id = ?", str(run_id)
            )
            return await exhausted(tx, [r["account"] for r in rows], strict=strict)

        return await self._tx(do)
