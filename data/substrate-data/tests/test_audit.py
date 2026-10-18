"""Audit trail: tamper-evident, exportable for a SIEM."""

from __future__ import annotations

import pytest

from substrate_data.api.routes.workloads import cef
from substrate_data.auth.principal import Principal


def user(name="alice"):
    return Principal(subject=name, name=name, kind="user", groups=frozenset())


def fill(store, n=5):
    for i in range(n):
        store.audit(user(), f"thing.{i}", f"main.s.t{i}", "success", f"req-{i}", detail=i)


def test_an_untouched_log_verifies(make_store):
    store = make_store()
    fill(store)
    assert store.verify_audit() == {"ok": True, "events_checked": 5, "first_bad_id": None}


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE audit_events SET outcome='denied' WHERE action='thing.2'",
        "UPDATE audit_events SET principal='user:mallory' WHERE action='thing.1'",
        "DELETE FROM audit_events WHERE action='thing.2'",
    ],
)
def test_editing_or_removing_an_event_is_detected(make_store, tamper):
    store = make_store()
    fill(store)
    with store.connection() as db:
        db.execute(tamper)
    result = store.verify_audit()
    assert result["ok"] is False and result["first_bad_id"] is not None


def test_retention_may_trim_the_oldest_events_without_breaking_the_rest(make_store):
    store = make_store()
    fill(store, 6)
    with store.connection() as db:
        db.execute("DELETE FROM audit_events WHERE id <= (SELECT min(id)+2 FROM audit_events)")
    assert store.verify_audit()["ok"] is True  # the log now starts later; the chain still holds


def test_collectors_can_resume_from_the_last_id(make_store):
    store = make_store()
    fill(store, 5)
    first = store.audit_since(0, 3)
    assert [e["action"] for e in first] == ["thing.0", "thing.1", "thing.2"]
    rest = store.audit_since(first[-1]["id"], 10)
    assert [e["action"] for e in rest] == ["thing.3", "thing.4"]
    assert store.audit_since(rest[-1]["id"], 10) == []


def test_cef_lines_escape_separators_and_carry_the_hash():
    line = cef(
        {
            "id": 7, "at": "2026-10-17T10:00:00Z", "principal": "user:a=b", "actor": None,
            "action": "x|y", "securable": "main.s.t", "outcome": "denied", "hash": "abc",
        }
    )  # fmt: skip
    assert line.startswith("CEF:0|Substrate|Data|1|x\\|y|x\\|y|7|")
    assert "suser=user:a\\=b" in line and "cs2=abc" in line and "externalId=7" in line


@pytest.mark.integration
def test_the_api_exports_and_verifies_for_admins_only(stack):
    from conftest import ADMIN

    with stack.http("alice", *ADMIN) as admin, stack.http("bob", "analysts") as bob:
        admin.post("/catalogs", json={"name": "audited_catalog"})
        reply = admin.get("/audit-events:export", params={"after": 0, "limit": 5000})
        assert reply.status_code == 200 and reply.headers["content-type"].startswith(
            "application/x-ndjson"
        )
        lines = reply.text.strip().splitlines()
        assert lines and int(reply.headers["x-next-after"]) > 0
        caught_up = admin.get(
            "/audit-events:export", params={"after": reply.headers["x-next-after"]}
        )
        assert "audited_catalog" not in caught_up.text
        assert admin.get("/audit-events:export", params={"format": "cef"}).text.startswith("CEF:0|")
        assert admin.get("/audit-events:verify").json()["ok"] is True
        assert bob.get("/audit-events:export").status_code == 403
        assert bob.get("/audit-events:verify").status_code == 403
