"""Fair use: rate, concurrency and daily scan budget, per principal."""

from __future__ import annotations

import pytest
from pydantic import SecretStr  # noqa: F401

from substrate_data.api.limits import RateLimiter, check_budget, check_concurrency
from substrate_data.api.routes.statements import ActiveStatements
from substrate_data.core.errors import Problem
from substrate_data.core.models import Settings


def settings(**fields) -> Settings:
    return Settings(_env_file=None, **fields)


def test_a_burst_is_allowed_then_requests_are_paced_with_retry_after():
    limiter = RateLimiter(settings(rate_limit_per_minute=60))
    for _ in range(10):  # the burst
        limiter.check("user:a")
    with pytest.raises(Problem) as refused:
        limiter.check("user:a")
    assert refused.value.status == 429 and refused.value.extensions["retry_after"] >= 1
    limiter.check("user:b")  # other principals are unaffected


def test_zero_disables_the_limit():
    limiter = RateLimiter(settings(rate_limit_per_minute=0))
    for _ in range(500):
        limiter.check("user:a")


def test_concurrency_is_capped_per_principal_and_per_tenant():
    active = ActiveStatements()
    cfg = settings(max_concurrent_statements=2, max_concurrent_statements_per_tenant=3)
    active.add("1", "user:a", "u", "r", "acme")
    active.add("2", "user:a", "u", "r", "acme")
    with pytest.raises(Problem) as own:
        check_concurrency(active, cfg, "user:a", "acme")
    assert own.value.code == "too-many-statements"
    check_concurrency(active, cfg, "user:b", "acme")
    active.add("3", "user:b", "u", "r", "acme")
    with pytest.raises(Problem) as shared:
        check_concurrency(active, cfg, "user:c", "acme")
    assert shared.value.code == "tenant-busy"
    check_concurrency(active, cfg, "user:c", "beta")
    active.pop("1")
    check_concurrency(active, cfg, "user:a", "acme")


def test_the_daily_budget_is_shared_through_the_catalog(make_store):
    store = make_store()
    cfg = settings(daily_scan_bytes_per_principal=1000)
    check_budget(store, cfg, "user:a")
    store.add_usage("user:a", 600)
    check_budget(store, cfg, "user:a")
    store.add_usage("user:a", 600)
    with pytest.raises(Problem) as over:
        check_budget(store, cfg, "user:a")
    assert over.value.status == 429 and over.value.extensions["retry_after"] > 0
    check_budget(store, cfg, "user:b")
    assert store.usage("user:a") == {"statements": 2, "bytes_planned": 1200}
    check_budget(store, settings(daily_scan_bytes_per_principal=0), "user:a")  # 0 = unlimited


@pytest.mark.integration
def test_the_api_enforces_them(stack, tmp_path):
    import polars as pl
    from substrate_data_client import DataError

    from conftest import ADMIN

    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "lim")
    path = tmp_path / "t.parquet"
    pl.DataFrame({"k": range(100)}).write_parquet(path)
    admin.upload("main.lim.t", [path])
    cfg = stack.app.state.services.settings
    saved = (cfg.rate_limit_per_minute, cfg.daily_scan_bytes_per_principal)
    try:
        cfg.daily_scan_bytes_per_principal = 1
        admin.sql("SELECT count(*) FROM main.lim.t")  # first statement plans some bytes
        with pytest.raises(DataError) as over:
            admin.sql("SELECT count(*) FROM main.lim.t")
        assert over.value.status == 429 and over.value.code == "quota-exceeded"
        cfg.daily_scan_bytes_per_principal = 0
        stack.app.state.limiter = RateLimiter(settings(rate_limit_per_minute=6))
        codes = []
        for _ in range(30):
            try:
                admin.request("GET", "/catalogs")
                codes.append(200)
            except DataError as exc:
                codes.append(exc.status)
        assert 429 in codes and codes[0] == 200
    finally:
        cfg.rate_limit_per_minute, cfg.daily_scan_bytes_per_principal = saved
        stack.app.state.limiter = RateLimiter(cfg)
