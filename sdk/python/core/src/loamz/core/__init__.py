"""Shared foundation of the Loamz client libraries.

- `loamz.core.exceptions`   typed errors, one class per problem `code` (and Azure-style aliases)
- `loamz.core.transport`    the sync and async HTTP transports: credentials, retries, trace context
- `loamz.core.credentials`  the token-credential protocol
- `loamz.core.paging`       lazy pagers (`ItemPaged`, `AsyncItemPaged`)
- `loamz.core.polling`      long-running operation pollers (`LROPoller`, `AsyncLROPoller`)
- `loamz.core.tenancy`      the naming rules every service agrees on (tenant aliases, volumes, groups, keys)
"""

from loamz.core import tenancy
from loamz.core.credentials import StaticTokenCredential, TokenCredential
from loamz.core.exceptions import HttpResponseError
from loamz.core.paging import AsyncItemPaged, ItemPaged
from loamz.core.polling import AsyncLROPoller, LROPoller
from loamz.core.transport import RetryPolicy

__all__ = [
    "AsyncItemPaged",
    "AsyncLROPoller",
    "HttpResponseError",
    "ItemPaged",
    "LROPoller",
    "RetryPolicy",
    "StaticTokenCredential",
    "TokenCredential",
    "tenancy",
]
