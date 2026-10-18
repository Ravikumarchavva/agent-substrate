"""Optional Arrow/Polars support. The core client needs only httpx; these load on first use."""

from __future__ import annotations

from typing import Any

HINT = "install the Arrow extra: pip install 'substrate-data-client[arrow]'"


def pyarrow() -> Any:
    try:
        import pyarrow as pa
    except ImportError:
        raise ImportError(f"Arrow results need pyarrow; {HINT}") from None
    return pa


def polars() -> Any:
    try:
        import polars as pl
    except ImportError:
        raise ImportError(f"DataFrame results need polars (and pyarrow); {HINT}") from None
    return pl
