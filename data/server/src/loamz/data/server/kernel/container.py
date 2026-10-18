"""Container: builds each dependency once, on first use, from `Settings`; closes them in reverse order.

It is the only composition root and knows no feature. Infrastructure every feature shares (the metadata store, the
token verifier, the compute controller, the rate limiter) is a property here; a feature declares how its own
service is built (a provider: a function of the container) next to that service, and anything that needs it asks
`container.get(provider)`. Tests replace a provider's instance with `container.put(provider, fake)`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import cached_property
from typing import Any

import httpx

from loamz.data.server.kernel.auth import TokenVerifier
from loamz.data.server.kernel.controller import ControllerClient
from loamz.data.server.kernel.limits import RateLimiter
from loamz.data.server.kernel.metadata import CatalogStore
from loamz.data.server.kernel.settings import Settings
from loamz.data.server.kernel.tickets import TicketSigner
from loamz.data.server.kernel.tls import internal_client_context

type Provider[T] = Callable[["Container"], T]


class Container:
    def __init__(
        self,
        settings: Settings,
        *,
        verifier: TokenVerifier | None = None,
        controller_transport: httpx.AsyncBaseTransport | None = None,
        worker_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._verifier = verifier
        self._controller_transport = controller_transport
        self._worker_transport = worker_transport
        self._built: dict[Callable[..., Any], Any] = {}
        self._closers: list[Callable[[], Awaitable[None] | None]] = []

    def get[T](self, provider: Provider[T]) -> T:
        """The one instance `provider` builds for this container."""
        if provider not in self._built:
            self._built[provider] = provider(self)
        return self._built[provider]

    def put[T](self, provider: Provider[T], instance: T) -> None:
        """Use `instance` wherever `provider` is asked for (tests, embedders)."""
        self._built[provider] = instance

    def on_close(self, closer: Callable[[], Awaitable[None] | None]) -> None:
        self._closers.append(closer)

    # ── infrastructure ──────────────────────────────────────────────────────
    @cached_property
    def store(self) -> CatalogStore:
        """Governed metadata: the namespace, grants, audit chain and workload records."""
        settings = self.settings
        store = CatalogStore(
            settings.metadata,
            url=settings.metadata_url.get_secret_value(),
            pool_size=settings.metadata_pool_size,
        )
        store.ensure_default(settings.admin_group)
        self.on_close(store.close)
        return store

    @cached_property
    def verifier(self) -> TokenVerifier:
        return self._verifier or TokenVerifier(self.settings)

    @cached_property
    def signer(self) -> TicketSigner:
        if self.settings.ticket_private_key_file is None:
            raise ValueError("The control plane requires LOAMZ_DATA_TICKET_PRIVATE_KEY_FILE")
        return TicketSigner(self.settings.ticket_private_key_file, self.settings.ticket_ttl_seconds)

    @cached_property
    def controller(self) -> ControllerClient:
        client = ControllerClient(self.settings, self._controller_transport)
        self.on_close(client.close)
        return client

    @cached_property
    def workers(self) -> httpx.AsyncClient:
        """Calls to attached compute (statements stream back from SQL workers)."""
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(10, read=self.settings.statement_timeout_seconds + 30),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            trust_env=False,
            verify=internal_client_context(self.settings),
            transport=self._worker_transport,
        )
        self.on_close(client.aclose)
        return client

    @cached_property
    def limiter(self) -> RateLimiter:
        return RateLimiter(self.settings)

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def aclose(self) -> None:
        while self._closers:
            result = self._closers.pop()()
            if result is not None:
                await result
