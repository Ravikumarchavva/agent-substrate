"""plan_quota_key + the daily plan-message quota's integration with
check_and_increment (already independently tested in test_doc_quota.py —
this file is about the NEW keying logic, not re-testing the Redis
primitive itself).
"""

from __future__ import annotations

from substrate.serving.monolith.routes.chat import plan_quota_key
from substrate.serving.shared.auth.claims import AuthClaims
from substrate.serving.shared.doc_quota import check_and_increment


class _FakeRedis:
    """Same minimal fake as test_doc_quota.py."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds


def test_quota_key_uses_tenant_id_when_project_scoped():
    user = AuthClaims(sub="anon-visitor-1", tenant_id="proj-abc123")
    assert plan_quota_key(user) == "proj-abc123"


def test_quota_key_falls_back_to_sub_when_tenant_is_default():
    user = AuthClaims(sub="user-42", tenant_id="default")
    assert plan_quota_key(user) == "user-42"


def test_quota_key_falls_back_to_sub_when_tenant_omitted():
    # tenant_id defaults to "default" per the AuthClaims model itself.
    user = AuthClaims(sub="user-42")
    assert plan_quota_key(user) == "user-42"


async def test_two_different_visitors_same_project_share_one_quota():
    """The core requirement: anonymous visitor A and B chatting with the
    SAME deployed chatbot (same tenant_id, different sub) must share one
    daily counter, not get one each."""
    redis = _FakeRedis()
    visitor_a = AuthClaims(sub="anon-aaa", tenant_id="proj-shared")
    visitor_b = AuthClaims(sub="anon-bbb", tenant_id="proj-shared")

    for _ in range(3):
        allowed, _ = await check_and_increment(
            redis, "planquota:message", plan_quota_key(visitor_a), limit=5
        )
        assert allowed
    for _ in range(2):
        allowed, remaining = await check_and_increment(
            redis, "planquota:message", plan_quota_key(visitor_b), limit=5
        )
    # 5 total increments across both visitors — exactly at the limit.
    assert allowed
    assert remaining == 0

    # A 6th message, from either visitor, is over the shared limit.
    allowed, _ = await check_and_increment(
        redis, "planquota:message", plan_quota_key(visitor_a), limit=5
    )
    assert not allowed


async def test_different_projects_do_not_share_a_quota():
    redis = _FakeRedis()
    project_1 = AuthClaims(sub="anon-x", tenant_id="proj-1")
    project_2 = AuthClaims(sub="anon-y", tenant_id="proj-2")

    for _ in range(5):
        allowed, _ = await check_and_increment(
            redis, "planquota:message", plan_quota_key(project_1), limit=5
        )
        assert allowed
    # project_1 is now exhausted, but project_2 is untouched.
    allowed, remaining = await check_and_increment(
        redis, "planquota:message", plan_quota_key(project_2), limit=5
    )
    assert allowed
    assert remaining == 4


async def test_non_project_scoped_users_get_independent_quotas():
    """The builder test-chat / direct dev usage case: no tenant_id set, so
    each user gets their own quota (today's implicit per-caller behavior),
    not a shared one."""
    redis = _FakeRedis()
    user_a = AuthClaims(sub="user-a")
    user_b = AuthClaims(sub="user-b")

    for _ in range(5):
        allowed, _ = await check_and_increment(
            redis, "planquota:message", plan_quota_key(user_a), limit=5
        )
        assert allowed
    # user_a exhausted; user_b (different sub, both tenant_id="default") is
    # NOT sharing a quota with user_a.
    allowed, remaining = await check_and_increment(
        redis, "planquota:message", plan_quota_key(user_b), limit=5
    )
    assert allowed
    assert remaining == 4
