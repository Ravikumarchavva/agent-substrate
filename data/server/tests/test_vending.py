"""Credential vending against a real STS: workers read only what one statement was planned against."""

from __future__ import annotations

import io
import time
from uuid import uuid4

import boto3
import duckdb
import polars as pl
import pytest
from pydantic import SecretStr

from loamz.data.server.features.compute.worker import StatementEngine
from loamz.data.server.features.tables.credentials import CredentialVendor, table_prefix
from loamz.data.server.kernel.settings import Settings
from loamz.data.storage.errors import LoamzError, http_status

pytestmark = pytest.mark.integration
BUCKET = "loamz-staging"  # a bucket the test store creates; stands in for the warehouse here


@pytest.fixture(scope="module")
def objects(seaweed):
    s3 = boto3.client(
        "s3",
        endpoint_url=seaweed.s3,
        aws_access_key_id=seaweed.access_key,
        aws_secret_access_key=seaweed.secret_key,
        region_name="us-east-1",
    )
    run = uuid4().hex[:8]
    keys = {}
    for table, values in (("a", [1, 2, 3]), ("b", [10, 20, 30])):
        buffer = io.BytesIO()
        pl.DataFrame({"k": values}).write_parquet(buffer)
        key = f"{run}/{table}/data/part.parquet"
        s3.put_object(Bucket=BUCKET, Key=key, Body=buffer.getvalue())
        keys[table] = f"s3://{BUCKET}/{key}"
    return keys


def settings(seaweed, *, keyless=False, **extra) -> Settings:
    key = SecretStr("" if keyless else seaweed.access_key)
    secret = SecretStr("" if keyless else seaweed.secret_key)
    return Settings(
        _env_file=None,
        s3_endpoint_url=seaweed.s3,
        s3_access_key_id=key,
        s3_secret_access_key=secret,
        warehouse=BUCKET,
        worker_threads=1,
        worker_memory_limit="512MB",
        **extra,
    )


def vendor(seaweed) -> CredentialVendor:
    return CredentialVendor(settings(seaweed, storage_sts_role_arn=seaweed.sts_role))


def ticket(files: dict[str, str], storage, sql: str) -> dict:
    tables = [
        {
            "alias": f"__t{i}",
            "table": name,
            "snapshot": "1",
            "files": [path],
            "columns": [["k", "BIGINT"]],
        }
        for i, (name, path) in enumerate(files.items())
    ]
    return {
        "jti": uuid4().hex,
        "sql": sql,
        "tables": tables,
        "storage": storage.claim() if storage else None,
        "limits": {"timeout_ms": 30000, "max_bytes": 10**8},
    }


def run(engine: StatementEngine, claims: dict) -> list:
    stream, _ = engine.execute(claims)
    import pyarrow as pa

    return pa.ipc.open_stream(b"".join(stream)).read_all().to_pylist()


def test_table_prefix_is_the_folder_holding_the_data_files():
    assert table_prefix(["s3://w/ns/t/data/x/y.parquet"]) == "s3://w/ns/t/"
    assert table_prefix(["s3://w/loose/file.parquet"]) == "s3://w/loose/"
    assert table_prefix([]) is None


def test_issued_credentials_read_their_tables_and_nothing_else(seaweed, objects):
    v = vendor(seaweed)
    creds = v.for_prefixes([table_prefix([objects["a"]])])
    assert creds.expires - time.time() > 600 and creds.scopes == (table_prefix([objects["a"]]),)
    keyless = StatementEngine(settings(seaweed, keyless=True))
    ok = ticket({"a": objects["a"]}, creds, "SELECT sum(k) AS s FROM __t0")
    assert run(keyless, ok) == [{"s": 6}]
    # The same credentials, asked to read the other table: refused by the object store itself.
    stolen = ticket({"b": objects["b"]}, creds, "SELECT sum(k) AS s FROM __t0")
    with pytest.raises(duckdb.Error) as refused:
        run(keyless, stolen)
    assert any(code in str(refused.value) for code in ("HTTP 403", "HTTP 404"))  # not served


def test_a_keyless_worker_cannot_read_anything_without_credentials(seaweed, objects):
    keyless = StatementEngine(settings(seaweed, keyless=True))
    with pytest.raises(duckdb.Error):
        run(keyless, ticket({"a": objects["a"]}, None, "SELECT sum(k) AS s FROM __t0"))


def test_each_statement_gets_only_the_tables_it_reads(seaweed, objects):
    v = vendor(seaweed)
    both = v.for_prefixes([table_prefix([objects["a"]]), table_prefix([objects["b"]])])
    only_a = v.for_prefixes([table_prefix([objects["a"]])])
    assert len(both.scopes) == 2 and len(only_a.scopes) == 1 and both.key_id != only_a.key_id
    keyless = StatementEngine(settings(seaweed, keyless=True))
    joined = ticket(
        {"a": objects["a"], "b": objects["b"]},
        both,
        "SELECT sum(x.k) + sum(y.k) AS s FROM __t0 x, __t1 y",
    )
    assert run(keyless, joined)[0]["s"] == 6 * 3 + 60 * 3


def test_concurrent_statements_with_different_credentials_do_not_mix(seaweed, objects):
    import threading

    v = vendor(seaweed)
    ca = v.for_prefixes([table_prefix([objects["a"]])])
    cb = v.for_prefixes([table_prefix([objects["b"]])])
    engine = StatementEngine(settings(seaweed, keyless=True, worker_concurrency=8))
    results, errors = [], []

    def go(files, creds, expected):
        try:
            for _ in range(5):
                got = run(engine, ticket(files, creds, "SELECT sum(k) AS s FROM __t0"))
                results.append(got[0]["s"] == expected)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=go, args=args)
        for args in [({"a": objects["a"]}, ca, 6), ({"b": objects["b"]}, cb, 60)] * 3
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and all(results) and len(results) == 30


def test_credentials_are_reused_while_fresh_and_policy_is_tightly_scoped(seaweed, objects):
    v = vendor(seaweed)
    calls = []
    real = v.client.assume_role
    v.client.assume_role = lambda **kw: (calls.append(kw), real(**kw))[1]
    scope = [table_prefix([objects["a"]])]
    first, second = v.for_prefixes(scope), v.for_prefixes(scope)
    assert first is second and len(calls) == 1
    policy = calls[0]["Policy"]
    assert '"s3:GetObject"' in policy and "PutObject" not in policy and "ListBucket" not in policy
    assert policy.count("arn:aws:s3:::") == 1 and calls[0]["DurationSeconds"] == 900


def test_vending_off_returns_nothing_and_bad_requests_fail_closed(seaweed, objects):
    assert CredentialVendor(settings(seaweed)).for_prefixes(["s3://x/y/"]) is None
    v = vendor(seaweed)
    with pytest.raises(LoamzError) as many:
        v.for_prefixes([f"s3://{BUCKET}/t{i}/" for i in range(26)])
    assert many.value.code == "invalid-request"
    with pytest.raises(RuntimeError, match="outside the warehouse"):  # a bug, never a user error
        v.for_prefixes(["s3://some-other-bucket/t/"])


def test_an_unreachable_sts_is_a_clear_503_not_a_fallback_to_a_wide_key(seaweed):
    broken = CredentialVendor(
        settings(
            seaweed,
            storage_sts_role_arn=seaweed.sts_role,
            storage_sts_endpoint="http://127.0.0.1:9",
        )
    )
    with pytest.raises(LoamzError) as down:
        broken.for_prefixes([f"s3://{BUCKET}/t/"])
    assert http_status(down.value) == 503


def test_expired_secrets_are_removed_from_the_worker(seaweed, objects):
    v = vendor(seaweed)
    engine = StatementEngine(settings(seaweed, keyless=True))
    creds = v.for_prefixes([table_prefix([objects["a"]])])
    run(engine, ticket({"a": objects["a"]}, creds, "SELECT 1 AS x FROM __t0 LIMIT 1"))
    assert len(engine.vended) == 1
    name = next(iter(engine.vended))
    engine.vended[name] = time.time() - 1  # pretend it expired
    other = v.for_prefixes([table_prefix([objects["b"]])])
    run(engine, ticket({"b": objects["b"]}, other, "SELECT 1 AS x FROM __t0 LIMIT 1"))
    assert name not in engine.vended and len(engine.vended) == 1
    left = [r[0] for r in engine.db.execute("SELECT name FROM duckdb_secrets()").fetchall()]
    assert name not in left


def test_the_worker_never_writes_secrets_to_disk(seaweed):
    engine = StatementEngine(settings(seaweed, keyless=True))
    with pytest.raises(duckdb.Error):
        engine.db.execute("CREATE PERSISTENT SECRET p (TYPE S3, KEY_ID 'x', SECRET 'y')")
