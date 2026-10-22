import httpx

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.settings import NotificationSettings
from loamz.server.agents.features.people.notifications.service import NotificationService
from loamz.server.agents.features.people.notifications.store import NotificationStore
from loamz.server.agents.testing.app import serving

ALICE = Caller(tenant_id="acme", user_id="alice")
BOB = Caller(tenant_id="acme", user_id="bob")
ALICE_ELSEWHERE = Caller(tenant_id="globex", user_id="alice")


def service(db, key: str = "") -> NotificationService:
    return NotificationService(NotificationStore(db), NotificationSettings(resend_api_key=key))


async def test_notifications_are_listed_counted_marked_read_and_private(db):
    s = service(db)
    first = await s.notify("acme", "alice", "Run finished", "all good", kind="task_run")
    await s.notify("acme", "alice", "x" * 500, link="/threads/1")
    await s.notify("acme", "bob", "Bob's")
    await s.notify("globex", "alice", "Elsewhere")

    page = await s.list(ALICE)
    assert page.unread == 2 and [n.title[:3] for n in page.items] == ["xxx", "Run"]
    assert len(page.items[0].title) == 200 and page.items[0].link == "/threads/1"
    assert (await s.list(BOB)).unread == 1 and (await s.list(ALICE_ELSEWHERE)).unread == 1

    await s.mark_read(ALICE, first.id)
    assert (await s.list(ALICE)).unread == 1
    try:
        await s.mark_read(BOB, first.id)
        raise AssertionError("another person's notification must look absent")
    except NotFoundError:
        pass
    await s.mark_all_read(ALICE)
    assert (await s.list(ALICE)).unread == 0 and (await s.list(BOB)).unread == 1


async def test_pages_follow_a_cursor(db):
    s = service(db)
    for i in range(5):
        await s.notify("acme", "alice", f"n{i}")
    one = await s.list(ALICE, page_size=2)
    two = await s.list(ALICE, page_size=2, page_token=one.next_page_token)
    three = await s.list(ALICE, page_size=2, page_token=two.next_page_token)
    seen = [n.title for p in (one, two, three) for n in p.items]
    assert seen == ["n4", "n3", "n2", "n1", "n0"] and three.next_page_token is None


async def test_erasing_a_user_or_a_tenant(db):
    s = service(db)
    await s.notify("acme", "alice", "a")
    await s.notify("acme", "bob", "b")
    assert await s.erase("acme", "alice") == 1
    assert (await s.list(BOB)).unread == 1
    assert await s.erase("acme") == 1


class _Http:
    def __init__(self, status: int = 200, fail: bool = False) -> None:
        self.status, self.fail, self.sent = status, fail, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers, json):
        if self.fail:
            raise ConnectionError("down")
        self.sent.append((url, json))
        return httpx.Response(self.status)


async def test_email_goes_through_resend_and_never_raises(db):
    http = _Http()
    assert await service(db, "k").email("a@b.c", "Hi", "text", client=http)
    assert http.sent[0][1]["to"] == ["a@b.c"]
    assert not await service(db, "").email("a@b.c", "Hi", "text", client=_Http())  # no key: no mail
    assert not await service(db, "k").email("", "Hi", "text", client=_Http())
    assert not await service(db, "k").email("a@b.c", "Hi", "t", client=_Http(status=422))
    assert not await service(db, "k").email("a@b.c", "Hi", "t", client=_Http(fail=True))


async def test_the_api_lists_and_marks_read(tmp_path):
    async with serving(tmp_path, only=("people.notifications",)) as served:
        from loamz.server.agents.features.people.notifications.service import notifications

        svc = served.container.get(notifications)
        made = await svc.notify("acme", "alice", "Hello", "body")
        await svc.notify("acme", "bob", "Not yours")
        async with served.client("acme", "alice") as c:
            got = (await c.get("/api/v1/notifications")).json()
            assert got["unread"] == 1 and [n["title"] for n in got["items"]] == ["Hello"]
            assert (await c.post(f"/api/v1/notifications/{made.id}:read")).status_code == 204
            assert (await c.get("/api/v1/notifications")).json()["unread"] == 0
            assert (await c.post("/api/v1/notifications/nope:read")).status_code == 404
            assert (await c.post("/api/v1/notifications:read-all")).status_code == 204
        async with served.client("acme", "bob") as c:
            assert (await c.post(f"/api/v1/notifications/{made.id}:read")).status_code == 404
