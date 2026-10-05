"""The server's access log is for what happened, not for the polls a page makes every few seconds."""

from __future__ import annotations

import logging

import pytest

from substrate_cloud.monolith.access_log import QuietPolls


def record(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", method, path, "1.1", status), None)


@pytest.mark.parametrize(
    "path",
    ["/agents", "/groups", "/scheduled", "/notifications", "/approvals", "/rate-limit/status", "/groups/0e6d67a7-423b-4cc0-82e6-d971c762504f/messages?after=31&wait=2", "/health"],
)
def test_a_successful_poll_is_not_logged(path):
    assert not QuietPolls().filter(record("GET", path, 200))


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/agents", 500),  # a poll that fails is news
        ("GET", "/agents", 401),
        ("POST", "/groups/0e6d67a7-423b-4cc0-82e6-d971c762504f/messages", 201),  # a message sent is not a poll
        ("PUT", "/agents/abc/pin", 200),
        ("GET", "/threads?limit=30", 200),
        ("GET", "/agents/abc", 200),  # one agent, opened on purpose
    ],
)
def test_everything_else_is_logged(method, path, status):
    assert QuietPolls().filter(record(method, path, status))
