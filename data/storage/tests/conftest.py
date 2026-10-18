import pytest

from loamz.data.storage.testing import postgres_available, postgres_schema, sqlite_database


@pytest.fixture(params=["sqlite", "postgres"])
async def db(request):
    """Every database test runs on both: SQL is written once."""
    if request.param == "sqlite":
        async with sqlite_database() as database:
            yield database
    else:
        if not await postgres_available():
            pytest.skip("no test PostgreSQL (set LOAMZ_TEST_POSTGRES_URL)")
        async with postgres_schema() as database:
            yield database
