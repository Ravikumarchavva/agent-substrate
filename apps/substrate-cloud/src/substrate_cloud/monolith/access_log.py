"""Keep the access log readable: a page polls a handful of endpoints every few seconds, and listing each success buries the requests that matter.

Failures of those same endpoints, and everything else (a message sent, a pin changed, a file opened), are still logged.
"""

from __future__ import annotations

import logging
import re

_POLLED = re.compile(
    r"^/(agents|groups|scheduled|notifications|approvals|health|rate-limit/status|groups/[0-9a-f-]+/messages)(\?.*)?$"
)


class QuietPolls(logging.Filter):
    """A ``uvicorn.access`` filter that drops successful ``GET``s of the polled endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            _client, method, path, _version, status = record.args  # type: ignore[misc]
        except (TypeError, ValueError):
            return True
        return not (method == "GET" and int(status) < 400 and _POLLED.match(str(path)))


def quiet_polls() -> None:
    logging.getLogger("uvicorn.access").addFilter(QuietPolls())
