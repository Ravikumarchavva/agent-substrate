import pytest

from loamz.data.storage import Storage
from loamz.data.storage.testing import postgres_available, postgres_schema, sqlite_database


async def _database(kind: str):
    if kind == "sqlite":
        return sqlite_database()
    if not await postgres_available():
        pytest.skip("no test PostgreSQL (set LOAMZ_TEST_POSTGRES_URL)")
    return postgres_schema()


@pytest.fixture(params=["sqlite", "postgres"])
async def db(request):
    """Every database test runs on both: SQL is written once."""
    async with await _database(request.param) as database:
        yield database


@pytest.fixture(params=["sqlite", "postgres"])
async def storage(request, tmp_path):
    """A `Storage` on each database, its file contents in a fresh folder."""
    async with await _database(request.param) as database:
        storage = Storage(database, tmp_path / "storage", file_quota_bytes=10**9)
        await storage.start()
        yield storage
