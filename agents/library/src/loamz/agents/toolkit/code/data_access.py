"""How a code-interpreter run gets governed warehouse data, with no network.

The platform supplies a :class:`DataAccess`; the tool asks it for a :class:`DataMount` for each run
and the runtime shows it to the code as a socket and a token file. What the code may read is decided
by the data service from the token, never by anything the model says.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from loamz.agents.kernel.types.run import RunScope

from .runtimes.base import DataMount

DATA_GUIDANCE = (
    " Governed warehouse data is available without network access: "
    "`import loamz.data.client as sd`, then `sd.search('word')` finds tables and views you may read, "
    "`sd.describe('catalog.schema.table')` shows columns and sample rows, and "
    "`sd.sql('SELECT ...')` returns a Polars DataFrame. Do the filtering and aggregation in SQL "
    "(GROUP BY, WHERE, LIMIT) and bring back only the answer; large results are refused. "
    "To report, save charts with matplotlib (`plt.savefig('chart.png')`) and spreadsheets with "
    "`df.write_excel('report.xlsx')` into the working directory, then link them."
)


@runtime_checkable
class DataAccess(Protocol):
    """Issues each run its own data access, or none."""

    async def grant(self, scope: RunScope, *, timeout_s: int) -> DataMount | None:
        """A mount valid for about ``timeout_s`` seconds for this run's user, or None if unavailable."""
        ...

    async def stop(self) -> None: ...


__all__ = ["DATA_GUIDANCE", "DataAccess"]
