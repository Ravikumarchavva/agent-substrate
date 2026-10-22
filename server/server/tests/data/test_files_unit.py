"""Files domain rules that need no storage: paths, ownership policy, quota arithmetic, multipart plans."""

from __future__ import annotations

import pytest

from loamz.engine.errors import LoamzError, http_status
from loamz.server.data.features.files.paths import normalize_path, normalize_prefix, overlaps
from loamz.server.data.features.files.policy import OwnerPrefixPolicy
from loamz.server.data.features.files.quota import check_quota, projected_bytes
from loamz.server.data.features.files.service import path_hash
from loamz.server.data.features.files.storage import MIN_PART, plan_parts
from loamz.server.data.kernel.principal import Principal

MiB = 1024 * 1024


# ------------------------------------------------------------------ paths
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a.txt", "a.txt"),
        ("docs//a.txt", "docs/a.txt"),
        ("users/u1/dir/file.bin", "users/u1/dir/file.bin"),
        ("café/ü.txt", "café/ü.txt"),
        ("é.txt", "é.txt"),  # NFC
        ("a b/c%2e.txt", "a b/c%2e.txt"),  # percent-looking text is just text
    ],
)
def test_paths_are_normalized(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/",
        "/etc/passwd",
        "../x",
        "a/../b",
        "a/./b",
        "a/..",
        "..",
        ".",
        "a\\b",
        "..\\x",
        "a\x00b",
        "a\nb",
        "dir/",
        "x/" + "y" * 300,
        "versions",
        "a/versions",
        "a/b:restore",
        "a/b:download-url",
        "p/" * 600,
    ],
)
def test_traversal_and_malformed_paths_are_refused(raw):
    with pytest.raises(LoamzError) as refused:
        normalize_path(raw)
    assert http_status(refused.value) == 400 and refused.value.code == "invalid-request"


def test_prefixes_keep_their_trailing_slash_and_refuse_traversal():
    assert normalize_prefix("") == ""
    assert normalize_prefix(None) == ""
    assert normalize_prefix("docs/") == "docs/"
    assert normalize_prefix("docs//sub/") == "docs/sub/"
    assert normalize_prefix("docs/rep") == "docs/rep"
    for raw in ("/", "/abs", "../", "a/../", "a\\b", "a\x00"):
        with pytest.raises(LoamzError):
            normalize_prefix(raw)


def test_overlap_detects_nesting_in_either_direction():
    assert overlaps("a/", "a/b/") and overlaps("a/b/", "a/") and overlaps("a/", "a/")
    assert not overlaps("a/", "b/")
    assert overlaps("a", "ab/")  # a plain string prefix: `a` matches `ab/`


def test_audit_records_a_hash_not_the_name():
    digest = path_hash("users/u1/secret-plan.pdf")
    assert len(digest) == 16 and "secret" not in digest
    assert digest == path_hash("users/u1/secret-plan.pdf") != path_hash("users/u1/other.pdf")


# ------------------------------------------------------------------ ownership policy
def agent(sub: str, **kw) -> Principal:
    return Principal(sub, sub, "user", actor="loamz-agents:primary", **kw)


policy = OwnerPrefixPolicy()


def test_a_delegated_token_owns_only_its_users_prefix():
    me = agent("alice")
    policy.authorize(me, "users/alice/a.txt", "write")
    policy.authorize(me, "shared/a.txt", "write")  # outside users/: grants decide
    for path in ("users/bob/a.txt", "users/bobby", "users"):
        with pytest.raises(LoamzError) as refused:
            policy.authorize(me, path, "read")
        assert http_status(refused.value) == 403 and refused.value.code == "forbidden"
    with pytest.raises(LoamzError):
        policy.authorize(me, "users/alicia/x", "write")  # a longer name is not "alice"


def test_ordinary_admin_and_tenant_admin_tokens_are_not_restricted():
    ordinary = Principal("sub-x", "x", "user")  # no actor: grants only
    policy.authorize(ordinary, "users/bob/a.txt", "write")
    policy.authorize(agent("alice", is_admin=True), "users/bob/a.txt", "write")
    policy.authorize(agent("alice", groups=frozenset({"tenant-admins"})), "users/bob/a", "read")


def test_prefix_checks_narrow_reads_and_refuse_writes_that_span_other_users():
    me = agent("alice")
    assert policy.authorize_prefix(me, "users/alice/docs/", "write")("anything")
    assert policy.authorize_prefix(me, "shared/", "write")("shared/x")
    for prefix in ("users/bob/", "users/bob/docs/"):
        with pytest.raises(LoamzError):
            policy.authorize_prefix(me, prefix, "read")
    for prefix in ("", "users", "users/", "users/al", "u"):
        with pytest.raises(LoamzError):
            policy.authorize_prefix(me, prefix, "write")
        see = policy.authorize_prefix(me, prefix, "read")
        assert see("users/alice/a.txt") and see("shared/a.txt") and see("users/")
        assert not see("users/bob/a.txt") and not see("users/bob/")


# ------------------------------------------------------------------ quota arithmetic
def test_projected_bytes_replaces_what_the_write_supersedes():
    assert projected_bytes(100, 30) == 130
    assert projected_bytes(100, 30, replacing=40) == 90
    assert projected_bytes(10, 5, replacing=40) == 5  # never negative


def test_quota_allows_up_to_and_including_the_limit_and_refuses_beyond():
    check_quota(100, 50, None)  # no quota
    check_quota(100, 50, 150)  # exactly at the limit
    check_quota(100, 60, 150, replacing=10)
    with pytest.raises(LoamzError) as over:
        check_quota(100, 51, 150)
    assert http_status(over.value) == 413 and over.value.code == "quota-exceeded"
    assert over.value.extensions() == {"limit": 150, "used": 100}
    with pytest.raises(LoamzError):
        check_quota(0, 1, 0)  # a zero quota is a read-only volume


# ------------------------------------------------------------------ multipart planning
def test_parts_cover_the_file_exactly_with_a_shorter_last_part():
    plan = plan_parts(40 * MiB, 16 * MiB)
    assert plan == [(1, 0, 16 * MiB), (2, 16 * MiB, 16 * MiB), (3, 32 * MiB, 8 * MiB)]
    assert sum(length for _n, _o, length in plan) == 40 * MiB


def test_a_file_that_is_an_exact_multiple_has_no_empty_part():
    plan = plan_parts(32 * MiB, 16 * MiB)
    assert [length for _n, _o, length in plan] == [16 * MiB, 16 * MiB]


def test_parts_are_contiguous_and_numbered_from_one():
    plan = plan_parts(100 * MiB + 1, 8 * MiB)
    assert [n for n, _o, _l in plan] == list(range(1, len(plan) + 1))
    assert all(plan[i][1] + plan[i][2] == plan[i + 1][1] for i in range(len(plan) - 1))
    assert plan[-1][2] == 4 * MiB + 1  # (100 MiB + 1) mod 8 MiB; 12 full parts before it
    assert plan[-1][1] + plan[-1][2] == 100 * MiB + 1


def test_too_many_parts_and_too_small_parts_are_refused():
    with pytest.raises(LoamzError) as huge:
        plan_parts(10_001 * MIN_PART, MIN_PART)
    assert http_status(huge.value) == 413
    assert len(plan_parts(10_000 * MIN_PART, MIN_PART)) == 10_000
    with pytest.raises(ValueError):
        plan_parts(100 * MiB, 1 * MiB)
    with pytest.raises(ValueError):
        plan_parts(0, 16 * MiB)
